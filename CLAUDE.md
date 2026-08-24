# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Design Principles

- **Enterprise best practices always.** Treat this as a high-scale production K8s environment in terms of design, structure, and operational patterns, even though it runs on a single node. This means proper namespace isolation, resource limits, health checks, RBAC, and GitOps workflows.
- **GitOps is the single source of truth.** All cluster state is declared in this repository. Manual `kubectl apply` or imperative changes are not acceptable. Everything flows through Flux CD reconciliation.

  **The exceptions are inventoried in `docs/host-state.md`** — node-level configuration, the two bootstrap secrets, and everything in UniFi and Cloudflare. Read it before assuming a rebuild restores something. Every item there fails silently: nothing alerts when the kubelet resolver config is missing, you find out when pods cannot resolve anything. **Adding host-level configuration means adding it to that page in the same PR.**
- **Security by default.** No secrets in the repo (use SOPS/Age encryption). Pre-commit hooks enforce this. All manifests should follow least-privilege principles.
- **Keep it simple.** Avoid over-engineering. The RPi has limited resources (300m CPU / 150Mi memory is a typical ceiling for a single workload). Don't add abstractions, features, or tooling that aren't needed yet.

## Development Principles

- All changes to this repo go through PRs - do not work on the `main` branch directly
- **Do NOT push to merged PRs.** Any deployment feedback (pod logs, Helm errors, `flux get` output) means the relevant PR is already merged. Always start a new branch for the fix.
- **Always branch from `origin/main`.** Run `git fetch origin` then `git checkout -b <branch> origin/main` before starting any new change. Never branch from a previous feature branch — it will carry commits that are already merged and cause conflicts.

### Validation

**CI runs this same pipeline** on every pull request and on pushes to `main` and `stable` (`.github/workflows/validate.yml`), so it is enforced regardless of who opened the PR — including Renovate and Dependabot, whose PRs never run the local git hooks. Tool versions are pinned in the workflow and kept current by Renovate via the `# renovate:` comment above each pin. The Flux CLI pin (`version:` under the `fluxcd/flux2/action` step) is deliberately the exception — it carries no such comment, because it tracks what the clusters run rather than the newest release.

**A pin without a `# renovate:` comment is invisible to Renovate**, so a tool added to the workflow needs one, or it silently freezes. Dependabot cannot substitute: its `github-actions` ecosystem bumps `uses:` refs only, not version strings in env vars or action inputs.

After any change to manifests, run the full validation pipeline from the repo root:

```bash
./scripts/validate-k3s.sh
```

**When Claude Code runs this pipeline, delegate to the `manifest-validator` subagent** (`.claude/agents/manifest-validator.md`) rather than running `./scripts/validate-k3s.sh` inline. It keeps the full step-by-step tool output (yamllint, trivy, kubeconform, etc.) out of the main conversation and reports back using the fixed pass/fail format defined in the preloaded `flux-validation-conventions` skill (`.claude/skills/flux-validation-conventions/`).

This runs twelve steps in order, **per site** (Akron, Eastbank — each reconciles a different subset of the repo, see Directory layout below). Sites and their layers are discovered automatically, see *How validation discovers what to build*:
1. **YAML lint** — `yamllint` against all files (ignores each site's `flux-system/` and `*.sops.yaml`)
2. **Flux build** — `flux build kustomization --dry-run` for each Flux Kustomization, for each site
3. **Kustomize build** — `kustomize build` of the site's entry point and each of its layers, concatenated into `$K3S_BUILD_DIR/k3s-built-<site>.yaml`, then hydrated with that site's `cluster-vars` the way Flux's `postBuild` does
4. **Schema validation** — `kubeconform -summary` against each site's built output
5. **Best practices** — `kube-score score --exit-one-on-warning` against each site's built output
6. **Security scan** — `trivy config ./` at every severity (whole repo, not per-site)
9. **Dependency coverage** — every directory with a pinned `image:` must be listed in `.github/dependabot.yml` and every listed directory must exist; and every `# renovate:` comment must be matched by a `customManagers` entry in `renovate.json` (whole repo, not per-site)
10. **Secrets encrypted** — every `*secret*.yaml` tracked by git must have `sops:` metadata and `ENC[]` values (whole repo, not per-site)
11. **CRD availability** — a Flux Kustomization with no `dependsOn` may only use custom resources whose CRDs it installs itself
7. **Variable references** — no `${VAR}` may survive step 3's substitution in each site's build output
8. **Policy** — `conftest test` against each site's built output using policies in `policy/`
12. **Alloy configs** — `alloy validate` against every Alloy river config in each site's built output, with that release's own `--stability.level` from its `extraArgs`

Step 2 gates steps 3–5, 7–8 and 12. Step 3 additionally gates steps 4, 5, 7, 8 and 12. Steps 1, 6, 9 and 10 always run independently.

**Warnings are errors.** Every step fails on any finding at any severity — trivy runs unfiltered, kube-score uses `--exit-one-on-warning`, conftest uses `--fail-on-warn`. A check that genuinely doesn't apply here gets an explicit exception (`.trivyignore.yaml`, a kube-score `--ignore-test`, a conftest policy change), never a severity floor. The exception carries a reason; a threshold silently hides the next finding too.

**Each step ends with a `CHECKED <n> <noun>` line, and `validate-k3s.sh` fails any step that reports zero or prints no such line.** This is the coverage invariant: because the pipeline discovers its own work (sites from a `sites/*/` glob, layers from the Flux `Kustomization` objects), discovery returning empty would make every per-site loop iterate zero times and exit 0 — five steps reporting PASS having validated nothing. The same shape of bug covers a file matcher that stops matching or an over-broad `--skip-dirs`. One check in the harness covers all of them, and covers steps added later for free — a new step just needs to print its own `CHECKED` line.

**Prefer each tool's own machine-readable format over parsing its JSON.** `kube-score -o ci`, `kubeconform -summary` and `conftest -o tap` are already one line per finding, so there's nothing to break when the tool changes internally. Trivy has no line-oriented format and is the one place a `jq` query is justified — it carries a `SchemaVersion` guard so a shape change fails loudly instead of silently reporting nothing. SARIF looks like the portable answer for all of them and is not: trivy buries severity in a multi-line `message.text` and the rule title in a separate `rules[]` array, and kube-score's SARIF drops the object name entirely and points every finding at line 1.

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

**A site's Secret is always a whole file, never a Kustomize patch over a shared one.** Kustomize merges `stringData` field by field, but a SOPS-encrypted file carries a single `sops:` metadata block describing the encryption of that whole document. Merging `stringData` from two separately-encrypted files leaves fields whose ciphertext the surviving block cannot decrypt, and Flux fails at decryption rather than at build time — so `kustomize build` renders it happily and the error only appears on the cluster. Each site's Secret must therefore contain every field it needs, including the ones identical to another site's.

**Put only actual secrets in a Secret.** A Secret is write-only to review: it cannot be diffed in a PR, conftest cannot inspect it, and a missing key reads as "desired state is empty", which is a delete. Configuration that merely *feels* private — DNS records, hostnames, IP ranges — belongs in a ConfigMap or `cluster-vars`, where it stays reviewable and policy-checkable. #232 moved PiHole's DNS records out for exactly this reason and left the Secret holding nothing, so the Secret was deleted outright. Repo visibility is a separate decision, tracked in #299; it is not what SOPS is for.

## Architecture

Three layers, composed many-to-many:

| Layer | Directory | Contains |
|---|---|---|
| **Component** | `base/<component>/` | Site-agnostic definitions. **No secrets, no site values** — anything that differs per site is a `${VAR}` placeholder. |
| **Site** | `sites/<site>/<layer>/` | This site's secrets, patches, and the list of `base/` components it wants. One subdirectory per Flux Kustomization. |
| **Entry point** | `clusters/<site>/` | Flux bootstrap manifests, `cluster-vars.yaml`, and the Flux `Kustomization` objects pointing at `sites/<site>/<layer>/`. |

The rule that makes this work: **`base/` never contains anything site-specific.** If a site would need to delete or override something in `base/`, that thing belongs in `sites/` instead. A `$patch: delete` against `base/` means the layering is wrong.

- Independent single-node K3s sites, each managed with **Flux CD + Kustomize + Helm**, sharing this one repo (Flux's standard multi-cluster monorepo pattern — no cluster federation, no shared control plane):
  - **Akron** (local, 8GB RAM) — every layer, and the only site that *stores* observability data. Deploys first.
  - **Eastbank** (remote, 8GB RAM) — infrastructure, infrastructure-config, dns-config, monitoring. Collects metrics but stores none of them.
  - **Lottage** (remote, 2GB RAM) — **out of scope**, scaffolding removed until the hardware is upgraded. Re-add by copying `sites/eastbank/` and `clusters/eastbank/`.
- **Claude does not touch any site. This is a boundary, not a capability gap.** No `kubectl`, no `flux`, no SSH, no HTTP requests to site services — write the commands out for the user to run and ask for the output. The separation between local dev and production is deliberate, and it is also how the user learns the system.

  Do not assume the network prevents it. The dev machine is on a separate VLAN but *is* routed to Akron: it resolves through Akron's PiHole (`10.6.1.53`) and reaches Akron's Traefik, so a `curl` at `grafana.homelab.home.arpa` will succeed. Restraint is the control here. Read-only probes still require asking first; anything that mutates state is out of scope regardless.
- **Rollout gating (Akron first):** Akron's Flux `GitRepository` watches `main`. Eastbank's watches a `stable` branch. After merging to `main` and confirming Akron is healthy, promote by running the **Promote to stable** workflow (`.github/workflows/promote-to-stable.yml`) from the Actions tab. There is no automatic cross-cluster gate — this is a manual, explicit step, and the workflow refuses to run unless you assert Akron is healthy.

  **Promotion is a fast-forward, never a merge or rebase.** `stable` is only ever moved to a commit that already exists on `main`, so the two branches share SHAs and can never diverge. Promoting by PR is what caused the old divergence: `main` allows only merge commits, `stable` allowed only rebase, so every promotion replayed main's work under fresh SHAs and git lost track of the fact that both branches held identical content. Conflicts then accumulated until a large PR made them unresolvable.

  `stable`'s ruleset is therefore reduced to `deletion` + `non_fast_forward` + `required_signatures`, with **no bypass actor**. A `pull_request` rule would block the workflow's push, and it cannot be bypassed — app bypass actors require an organization, and this repo is user-owned. That is the better outcome: with no bypass, `non_fast_forward` applies to everyone including the repository owner, so `stable` can only ever move forward and nobody can rewrite it. The workflow never passes `--force` either.

  **`main` carries `required_signatures` for the same reason `stable` does.** An unsigned commit that reaches `main` cannot be repaired — `non_fast_forward` with no bypass means `main`'s history is immutable — and every later promotion introduces it to `stable` as a new commit, so the push is rejected until `stable`'s ruleset is temporarily disabled. #294 landed one and cost exactly that. Since `main` allows only merge commits, a PR's commits arrive verbatim, which is what lets the rule see them; a squash or rebase merge would replace them with a GitHub-signed commit and hide the problem until promotion.

### Naming

**`docs/naming-convention.md` is the reference for every hostname, site identifier and node name.** Read it before choosing a name for anything — a service, an ingress host, a node, a DNS label.

It describes the **target** state (`<role>.<site>.internal.zerpzorp.com`), which is being rolled out by #228 and is not live yet. Everything currently deployed is still on `home.arpa` (`grafana.homelab.home.arpa`), so do not rename existing resources to match it opportunistically — the cutover is iteration 4 of #228 and lands with TLS and the remote-write change in one PR. Until then the convention governs *new* names and reviews, not migrations.

It also covers **Kubernetes object names** (function in the name, identity in `app.kubernetes.io/*` labels, no site prefix, no kind suffix). Those rules are equally forward-looking — most existing object names predate them. Apply them to new objects and to anything an iteration of #228 is already touching; do not open a mass-rename PR.

The part that already applies: the **site identifier** (`akron`, `eastbank`, `lottage`) is used verbatim for the UniFi site, `sites/<site>/`, `clusters/<site>/`, `SITE_NAME` and the future DNS label. That is why those all match today, and a new site must keep them matching.

### Directory layout

```
base/                            # Site-agnostic components. No secrets. No site values.
  dns/                           # PiHole + Unbound
  traefik/                       # Ingress controller
  metallb/                       # L2 load balancer
  system-upgrade-controller/
  cert-manager/                  # cert-manager HelmRelease (installs the CRDs)
  coredns-config/                # k3s CoreDNS custom stub (needs no CRDs, but grouped with config)
  metallb-config/                # MetalLB IP pools (needs MetalLB CRDs)
  traefik-routes/                # PiHole IngressRoute (needs Traefik CRDs)
  cert-manager-config/           # ClusterIssuers + wildcard Certificate (needs cert-manager's CRDs)
  pihole-sync/                   # Syncs DNS blocklists into each site's own local PiHole
  metrics-collection/            # node-exporter + kube-state-metrics + Alloy, remote-writing to Akron

sites/<site>/                    # Everything specific to one K3s cluster
  infrastructure/                # kustomization.yaml picking base components + this site's
                                 # cloudflare-secret and dnsmasq records (site.conf)
  infrastructure-config/         # kustomization.yaml picking the CRD-dependent base components
  dns-config/                    # kustomization.yaml picking base/pihole-sync + this site's pihole-clients.yaml
  monitoring/                    # Every site: base/metrics-collection. Akron adds the storage
                                 # side — Prometheus + Grafana + Loki + Alloy (logs).
                                 # Eastbank adds Unpoller, which polls every site's UniFi
  apps/                          # Eastbank only: NetworkOptimizer

clusters/<site>/                 # Flux entry point — managed by the flux-system Kustomization
  flux-system/                   # Flux's own manifests (managed by flux bootstrap, do not edit)
  flux-system-local/             # Patches applied over flux-system/ (kube-score ignores, etc.)
  cluster-vars.yaml              # Per-site ConfigMap (DNS_DOMAIN, HOSTNAME, METALLB_*, NODE_IP) injected via postBuild.substituteFrom
  infrastructure.yaml            # Flux Kustomization -> sites/<site>/infrastructure
  monitoring.yaml                # -> sites/<site>/monitoring
  infrastructure-config.yaml     # -> sites/<site>/infrastructure-config
  dns-config.yaml                # -> sites/<site>/dns-config
  apps.yaml                      # -> sites/<site>/apps  (Eastbank only)

```

### Why `cluster-vars.yaml` lives in `clusters/`, not `sites/`

It is site-specific, so `sites/<site>/` looks like the obvious home. The distinction that decides it is *who reconciles it*:

- Everything under `sites/<site>/<layer>/` is reconciled **by** a layer's own Flux `Kustomization`, and is an output — manifests that get applied.
- `cluster-vars.yaml` is reconciled by the `flux-system` Kustomization itself, before any layer runs, and is an **input to** every other layer via `postBuild.substituteFrom`. A layer cannot supply the variables that layer is substituted with.

So the rule is: **`clusters/<site>/` is what Flux needs in order to reconcile this site** — its bootstrap manifests, its credentials, its entry point, and its identity. **`sites/<site>/` is what this site deploys.** `cluster-vars` is identity, not deployment.

There is also a mechanical reason. `clusters/<site>/kustomization.yaml` would have to reach into `sites/<site>/` to pick the file up, and kustomize's load restrictions only allow crossing directories via a directory that has its own `kustomization.yaml` — so `sites/<site>/` would need a top-level one whose shape differs from every per-layer one beneath it. Cost with no benefit.

**Renaming a Flux `Kustomization` object is destructive.** `flux-system` prunes the old name and cascade-deletes everything it owned, PVCs included. Change `spec.path` freely; treat `metadata.name` as load-bearing.

**Renaming any resource guarded by a validating webhook that rejects duplicates deadlocks Flux.** Flux applies before it prunes, so both the old and new names exist at apply time. If a webhook rejects the new object *because* the old one still holds the same value, the apply fails, the prune never runs, and every subsequent reconcile fails identically — it does not self-heal.

Hit for real renaming MetalLB's `IPAddressPool` (#230): `ipaddresspoolvalidationwebhook.metallb.io` denied `lan` because its CIDR overlapped `homelab-pool`, which was still there precisely because the apply had failed. The fix is to delete the old objects by hand, dependents first, then `flux reconcile`. Assume this shape for any CRD whose webhook enforces uniqueness on a field — an overlapping CIDR, a duplicate hostname, a claimed port. Pinning the value elsewhere (these Services pin their IPs with `metallb.universe.tf/loadBalancerIPs`) protects the *assignment*, not the *apply*, so it is not evidence the rename is safe.

**Two different "per-site" concepts coexist — don't confuse them:**
- **Per-K3s-cluster** — `sites/akron/`, `sites/eastbank/`. One directory per physical cluster.
- **Per-UniFi-site** — the `[[unifi.controller]]` stanzas in Unpoller's config (`sites/eastbank/monitoring/unpoller/`). One Unpoller at Eastbank polls *every* site's UniFi controller over the Site Magic VPN, including Lottage's, which is alive and correct even though Lottage's K3s cluster no longer exists here. It used to be three Unpoller instances on Akron; see #120.

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

**Eastbank** (watches `stable`) — `clusters/eastbank/` → `infrastructure` (`sites/eastbank/infrastructure`) → `monitoring`, `infrastructure-config`, `dns-config` and `apps`, all depending on `infrastructure`.

### Observability: collect everywhere, store at Akron

Akron is the only site with storage that tolerates a Prometheus TSDB (USB3 NVMe); every other site boots from an SD card, where TSDB write amplification is the classic way to kill the card. So the split is:

- **Collection is identical at every site** — `base/metrics-collection/` deploys node-exporter, kube-state-metrics and an Alloy collector (`alloy-metrics`) that scrapes them plus kubelet, cAdvisor, kube-apiserver and Unbound, then remote-writes the result. Akron runs this too; it is not a remote-site special case.
- **Storage is Akron-only** — `sites/akron/monitoring/` adds Prometheus, Grafana, Loki, Alertmanager and the log-collecting `alloy` DaemonSet.
- **Unpoller is the exception that proves the rule** — it runs at Eastbank (`sites/eastbank/monitoring/unpoller/`), reaching every site's UniFi controller over the VPN and writing metrics and logs back to Akron. Nothing about polling needs to sit next to the storage, and Akron's headroom is the scarce resource.

`${PROMETHEUS_REMOTE_WRITE_URL}` and `${LOKI_PUSH_URL}` are the only things that differ: Akron writes to the Prometheus and Loki beside it, Eastbank writes to `http://10.6.1.80/...` over the Site Magic VPN. Both endpoints are path-scoped Traefik `IngressRoute`s (`sites/akron/monitoring/prometheus-remotewrite-ingressroute.yaml`, `loki-push-ingressroute.yaml`), so only `/api/v1/write` and `/loki/api/v1/push` are published — not the query UIs. Deliberately IPs, not hostnames: nothing in the telemetry path should depend on cross-site DNS.

**Pod logs are collected at Akron only.** Other sites run `alloy-metrics`, which ships metrics and accepts application pushes but does not read pod logs. So at a remote site a pod's logs die with the pod: `kubectl logs --previous` reaches one generation back, and a crashlooping pod destroys its own evidence. Capture with `kubectl logs -f` while it is happening.

**The collector is the only thing at a site that crosses the VPN.** An application that produces logs pushes them to its local `alloy-metrics` (which serves Loki's push API on `alloy-metrics:3100`) and Alloy forwards them on — Unpoller is the first of these. Pointing an app straight at Akron would work and is wrong twice over: it couples every app to a remote endpoint, and it loses the WAL, so a WAN outage becomes a hole in the data instead of a delay. Note `loki.write`'s `wal` block is **experimental** upstream, unlike `prometheus.remote_write`'s.

**Two settings are load-bearing and must stay in sync**, or a remote site silently loses data across an outage:

| Setting | Where | Why |
|---|---|---|
| `wal.max_keepalive_time: 24h` | `base/metrics-collection/alloy-metrics.yaml` | How long unsent samples survive on disk. Chart default is **8h**. |
| `tsdb.outOfOrderTimeWindow: 24h` | `sites/akron/monitoring/kube-prometheus-stack.yaml` | Prometheus rejects samples older than its head block (~2h) as "out of bounds". Without this, a replayed buffer is rejected wholesale and the WAL is decorative. |

**All cardinality-trimming lives in the collector config**, in one place. It used to sit in kube-prometheus-stack's `cAdvisorMetricRelabelings` / `kubeApiServer.metricRelabelings`; those are gone, and the chart's own scraping (`kubelet`, `kubeApiServer`, `nodeExporter`, `kubeStateMetrics`) is disabled so Akron doesn't double-collect. Add drop rules to `alloy-metrics.alloy`, never back to the chart.

**Helm values in this repo are not validated by `./scripts/validate-k3s.sh`.** `kustomize build` and `flux build` treat a `HelmRelease`'s `values:` as opaque YAML — the chart is only rendered on the cluster at reconcile time, and none of these charts ship a `values.schema.json`. A wrong or misspelled value passes every step and fails after merge. Before changing chart values, render them locally with `flate` (see below) and read the output.

The Alloy config is the exception: it *is* checked, by validation step 12, because a syntax or argument-name error CrashLoops the collector at *every* site. Each config is a real `.alloy` file next to its `HelmRelease` (`base/metrics-collection/alloy-metrics.alloy`, `sites/akron/monitoring/alloy.alloy`), generated into a ConfigMap the release points at with `configMap.create: false`. The step reads each config out of the built output — that is where it is already hydrated with the site's `cluster-vars` — and runs `alloy validate` with the release's own `--stability.level`, so a public-preview component only passes in a release that opted into one. Nothing is hand-listed: a new Alloy release is picked up automatically, and inlining a config back into `values.alloy.configMap.content` fails the step rather than skipping it.

The generated ConfigMaps set `disableNameSuffixHash: true`. Kustomize's nameReference transformer does not know about `HelmRelease.spec.values.alloy.configMap.name`, so a hashed name would leave the release pointing at a ConfigMap that does not exist. No reload is lost — the chart's `configReloader` sidecar watches the mounted file.

To check one by hand while iterating:

```bash
brew install grafana/grafana/alloy   # plain `brew install alloy` is an unrelated package
alloy fmt base/metrics-collection/alloy-metrics.alloy
alloy validate base/metrics-collection/alloy-metrics.alloy   # ${VAR} placeholders make this fail; step 12 is the real check
```

Note this covers the config, not the chart values around it — `extraPorts`, `mounts` and the rest are still opaque YAML.

**To read rendered charts, use `flate`, not `flux-local`.** `flux-local` (#213's Option B) is deprecated and sunsetted upstream in favour of [`flate`](https://github.com/home-operations/flate), and it no longer works here anyway — it shells out to `flux build ks --kustomization-file /dev/stdin`, which segfaults on flux 2.9.4. `flate` renders the whole cluster with the upstream helm/kustomize/source SDKs, applies `postBuild` substitution, skips secrets by default, and caches charts on disk:

```bash
flate build hr -p clusters/akron        # every chart-rendered object, ~2s warm
```

That output is the only place workloads like Prometheus, Grafana, Loki and the Alloys are visible at all — everything else in the pipeline stops at the `HelmRelease`. It is what makes a values bug like `alloy.storagePath` mounting nothing legible: grep the rendered StatefulSet for the mount instead of trusting the values block.

**Deliberately not a validation step.** It downloads charts, so it needs network and an external failure mode on every run, in a pipeline that is otherwise hermetic. Render by hand when changing chart values or reviewing a chart bump; leave the pipeline alone. Note `flate diff` does not work in this repo at all — go-git rejects the `worktreeconfig` extension git sets here.

### Variable substitution

Each site's `cluster-vars.yaml` defines its own `${DNS_DOMAIN}`, `${HOSTNAME}`, `${METALLB_ADDRESS_RANGE}`, `${METALLB_TRAEFIK_IP}`, `${METALLB_PIHOLE_IP}`, `${NODE_IP}`, `${SITE_NAME}`, `${PROMETHEUS_REMOTE_WRITE_URL}`. Use these placeholders directly in `base/` manifests — Flux substitutes them at reconcile time via `postBuild.substituteFrom`, from that site's own ConfigMap only (there is no cross-site fallback).

Plain `kustomize build` does not perform this substitution, and neither does `flux build --dry-run` (with no cluster, it cannot read the `substituteFrom` ConfigMap). Validation step 3 therefore does it itself, so every downstream step sees the values that actually get applied instead of a `${METALLB_TRAEFIK_IP}` string where an IP belongs. Step 7 then fails on anything still looking like `${...}` — an undefined variable, a typo, or envsubst syntax the substitution pass does not implement (`${VAR:=default}`, unused here).

Resources annotated `kustomize.toolkit.fluxcd.io/substitute: disabled` are left alone by both steps: their `${...}` tokens belong to the target application (a Grafana dashboard's `${DS_PROMETHEUS}`), and Flux never touches them either.

**When adding a new variable:** add it to every site's `cluster-vars.yaml` that reconciles the manifest using it, before (or in the same PR as) that manifest.

### Adding a component

Layers are grouped by **reconcile semantics, not by namespace** — `infrastructure-config` spans `metallb-system` and `kube-system`, and `dns-config` exists separately from it because a gravity rebuild needs `force: true` and a 20m timeout, not because PiHole is in the `dns` namespace.

**Shared across sites** (the usual case):
1. Create `base/<component>/` with a `kustomization.yaml` listing its resources. Use `${VAR}` for anything site-specific; put no secrets here.
2. Add `- ../../../base/<component>` to each `sites/<site>/<layer>/kustomization.yaml` that should get it — `infrastructure/` for plain resources, `infrastructure-config/` if it needs CRDs the infrastructure layer installs.
3. No changes to `clusters/` or the validation scripts.

Opting a site out is just *not adding the line* — there is no delete-patch pattern, by design.

**Specific to one site** (e.g. Akron's monitoring stack): create it under `sites/<site>/<layer>/` and add it to that layer's `kustomization.yaml`. Nothing else changes.

**The `apps` layer exists at Eastbank only** (`sites/eastbank/apps/`, `clusters/eastbank/apps.yaml`), holding NetworkOptimizer. Akron has none. Add one to another site with the steps in *Adding a new top-level Flux Kustomization* below.

**`dns-config` depends on `infrastructure`, not `apps`.** It used to depend on `apps`, but that was incidental ordering — `pihole-sync` talks to `pihole-web`, which the infrastructure layer owns. There is no dependency in either direction; do not reintroduce one.

**NetworkOptimizer's UniFi credentials are the only state here not reproducible from git.** They are entered through its web UI and stored in SQLite on its PVC, so anything that replaces that volume — a node rename, a PVC rebuild — means re-entering them. Its admin password comes from the SOPS secret; the UniFi credentials do not.

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

**Every Traefik CR goes in `base/traefik-routes/`, never next to the HelmRelease in `base/traefik/`.** `traefik-routes` is reconciled by `infrastructure-config`, which `dependsOn: infrastructure`, so the `IngressRoute` CRD is installed by the time these apply. A Traefik CR in `base/traefik/` ships in the same Kustomization as the Helm release that provides its CRD — which works on a cluster that already has Traefik, and fails on a fresh one with `no matches for kind "IngressRoute"`. This shipped twice before validation step 11 started catching it.

**`base/cert-manager/` and `base/cert-manager-config/` follow the same split, for the same reason.** `cert-manager/` holds the Namespace + HelmRelease and lives in `infrastructure` because it installs the `ClusterIssuer`/`Certificate` CRDs. `cert-manager-config/` holds the `ClusterIssuer`s and the per-site wildcard `Certificate` — all CRs — and lives in `infrastructure-config`, which `dependsOn: infrastructure`. A `ClusterIssuer` in `cert-manager/` would hit the same fresh-cluster failure as an `IngressRoute` in `base/traefik/`.

### Dependency management

Two tools, non-overlapping scopes:

- **Renovate** (`renovate.json`) — Helm chart versions in `HelmRelease` resources (the `flux` manager), plus two `customManagers`: the k3s version in `base/system-upgrade-controller/plans.yaml`, and the validation tool pins in `.github/workflows/validate.yml`. Runs on weekends.
- **Dependabot** (`.github/dependabot.yml`) — container images pinned directly in manifests (`pihole`, `unbound`, `unbound-exporter`, `python`, `system-upgrade-controller`), plus GitHub Actions. Its docker ecosystem does read `image:` fields out of Kubernetes YAML.

Dependabot lists explicit directories, so **a component that moves or gains an image needs a `.github/dependabot.yml` entry**. Validation step 9 enforces this in both directions — it exists because this config silently went stale when directories last moved, and nobody noticed images had stopped being updated.

Renovate's `customManagers` fail the same silent way: a `# renovate:` comment whose regex doesn't match, or that sits in a file outside the manager's `managerFilePatterns`, produces no error — Renovate simply never proposes a bump. Step 9 checks both halves together, which is why it covers Renovate as well as Dependabot despite the script's filename.

Flux's own controller images (`clusters/*/flux-system/`) are excluded from both: they are upgraded with the Flux CLI, see `docs/runbooks/flux-upgrades.md`.

### Known dead ends

Things that look obviously fixable, have been attempted repeatedly, and are not. Check here before starting.

**`pi.hole` cannot be made to resolve anywhere useful.** FTL synthesizes an A record for `pi.hole` at runtime pointing at the pod's own interface address, and that synthesis outranks `FTLCONF_dns_hosts`, dnsmasq `address=` entries, and Traefik `IngressRoute` host rules. The pod IP is never written to `pihole.toml`, so no `FTLCONF_*` key can reach it. Setting `webserver_domain` or `piholePTR=NONE` does not stop it either.

Attempted in PRs #39, #58/#59, #61, #62, #81, #82, #83; all removed as no-ops by #84. Re-attempted and reverted again in #208, including the theory that MetalLB's external IP would change the answer — it does not, because FTL reads its own interface, not the Service.

Verified still true on `pihole:2026.07.2`, 2026-08-07:

```
$ dig +short pi.hole @10.6.1.53
10.42.0.33          # pod IP, not the LoadBalancer IP
```

Use `pihole.${HOSTNAME}` or the LoadBalancer IP directly. Re-run that `dig` before spending time on it again — a `10.42.x.x` answer means nothing has changed. The only untried avenue is making the pod CIDR routable from the LAN with a static route, which trades a LAN-wide route into the pod network for a convenience hostname, and lands the UI on `:8080`.

### Hardware constraints

All images must support **ARM64** (Raspberry Pi 4B). Verify ARM64 availability before pinning any image. All workloads must declare requests and limits, and storage limits (if applicable).
