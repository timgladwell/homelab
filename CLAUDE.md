# CLAUDE.md

Guidance for Claude Code in this repository. It holds only what applies at the
system level; anything tied to specific code is a comment on that code, so read
the file before changing it.

Where every kind of information lives — comments, docs, ADRs, runbooks, issues,
PRs — and how temporary things are tagged:

@CONTRIBUTING.md

## Boundaries

**Claude does not touch any site.** No `kubectl`, no `flux`, no SSH, no HTTP
requests to site services: write the commands out for the user to run and ask for
the output. The separation between local development and production is
deliberate, and it is how the user learns the system. Do not assume the network
prevents it — the dev machine resolves through Akron's PiHole and reaches Akron's
Traefik, so a `curl` would succeed. Restraint is the control.

**The one exception is read-only observability queries through the `grafana` MCP
server**, which runs `mcp-grafana --disable-write` with a Viewer-scoped service
account ([runbook](docs/runbooks/grafana-query-access.md)). Ask before the first
query in a session; after that, iterate freely (#220). The exception is the
mechanism, not the destination: a raw `curl` at Grafana is still out.

## Principles

- **Enterprise best practices.** Treat this as a high-scale production
  environment in design, structure and operations, though each site is one node:
  namespace isolation, resource limits, health checks, RBAC, GitOps.
- **GitOps is the single source of truth.** All cluster state is declared here
  and reconciled by Flux; no imperative changes. The exceptions — node
  configuration, the two bootstrap secrets, everything in UniFi and Cloudflare —
  are inventoried in [State That Is Not In Git](docs/current-state/host-state.md).
  Every item there fails silently, so **adding host-level configuration means
  adding it to that page in the same PR.**
- **Security by default.** No unencrypted secrets in the repo (SOPS/age, enforced
  by hooks and validation step 10); only actual secrets in a Secret; least
  privilege everywhere. **Data from outside the system reaches the DOM through
  `textContent`, never `innerHTML`**, and is not encoded at the source — escaping
  belongs at the sink. Read "outside" broadly: a DNS query log is attacker-chosen
  (`base/landing/` carries the reasoning beside each such value).
- **Keep it simple.** Raspberry Pi 4B nodes: about 300m CPU / 150Mi memory is a
  typical ceiling for one workload. No abstractions, features or tooling that
  aren't needed yet.
- **Every image supports ARM64**, and every workload declares requests and limits
  (and storage limits where applicable).

## Workflow

- All changes go through PRs. Always `git fetch origin` and branch from
  `origin/main`, never from another feature branch.
- **Never push to a merged PR.** Deployment feedback (pod logs, Helm errors,
  `flux get` output) means the PR is already merged; start a new branch.
- **Commits must be signed to reach `main`**, and an unsigned commit on `main`
  cannot be repaired ([ADR 0002](docs/adr/0002-rollout-across-sites.md)).
- **Validate after any manifest change** by delegating to the
  `manifest-validator` subagent, rather than running `./scripts/validate-k3s.sh`
  inline. CI runs the same pipeline on every PR. Warnings are errors; a check that
  doesn't apply gets an explicit exception with a reason, never a severity floor.

## Architecture

Independent single-node K3s sites sharing this repo (Flux's multi-cluster
monorepo pattern):

- **Akron** (local, 8 GB) — every layer, and the only site that stores
  observability data. Watches `main`.
- **Eastbank** (remote, 8 GB) — collects metrics but stores none. Watches
  `stable`.
- **Lottage** (remote, 2 GB) — out of scope until its hardware is upgraded; its
  UniFi controller is still polled by Eastbank's Unpoller.

| Decision | ADR |
|---|---|
| `base/` components, `sites/` composition, `clusters/` entry points; `cluster-vars` vs `network-vars` | [0004](docs/adr/0004-repository-layering.md) |
| Akron first; `stable` only fast-forwarded by the *Promote to stable* workflow | [0002](docs/adr/0002-rollout-across-sites.md) |
| Collect at every site, store at Akron; what gets collected | [0003](docs/adr/0003-observability-collection-and-storage.md) |
| PostgreSQL as a platform service | [0001](docs/adr/0001-postgresql-platform-service.md) |

**`base/` never contains anything site-specific.** A site opts into a component
by listing it; a `$patch: delete` against `base/` means the layering is wrong.

**Names** — hostnames, site identifiers, node names, Kubernetes objects — follow
[the naming convention](docs/current-state/naming-convention.md). Read it before
naming anything. The site identifier (`akron`, `eastbank`) is used verbatim for
the UniFi site, `sites/<site>/`, `clusters/<site>/` and `SITE_NAME`.

```
base/<component>/        site-agnostic; ${VAR} for anything per-site
sites/<site>/<layer>/    one directory per Flux Kustomization: infrastructure,
                         infrastructure-config, dns-config, monitoring, apps
clusters/common/         network-vars.yaml — estate-wide variables
clusters/<site>/         flux-system/ (do not edit), cluster-vars.yaml, and the
                         Flux Kustomization objects
docs/current-state/      state that code does not define
docs/adr/                one decision per system-level design question
docs/runbooks/           procedures for a specific outcome, indexed in
                         docs/runbooks.md by kind
scripts/validate-k3s.sh  the validation pipeline; steps in scripts/validate/
```

## Before changing…

| Change | Follow |
|---|---|
| Adding or removing a component, layer, variable, secret or ingress route | [Adding or Removing a Component, Layer or Variable](docs/runbooks/adding-components.md) |
| Renaming a Flux Kustomization, moving a resource between them, or renaming anything a webhook keeps unique | [Renaming or Moving Across Flux Kustomizations](docs/runbooks/flux-kustomization-changes.md) — these can delete PVCs or deadlock Flux |
| Any memory limit | [Sizing a Container's Memory Limit](docs/runbooks/memory-sizing.md) |
| Helm chart values or versions | Render and read them (`flate`, in [the development runbook](docs/runbooks/development-environment.md)); chart upgrades have their own [runbooks](docs/runbooks.md) |
| Flux itself | [Flux Upgrades](docs/runbooks/flux-upgrades.md) — the CI pin and every box's CLI are hand work |
| Host-level configuration | [State That Is Not In Git](docs/current-state/host-state.md), same PR |
| Anything that looks obviously fixable but has a history of failing, such as making `pi.hole` resolve | The comment beside the code that would change (`base/dns/pihole-deployment.yaml` for `pi.hole`) records what was tried |
