# Flux Upgrades

## Usually, you just merge the Renovate PR

Renovate's `flux` manager regenerates the **entire** `gotk-components.yaml` for both sites — CRD schemas, RBAC, NetworkPolicies — not just image tags. A Flux bump is therefore normally review-and-merge, minor releases included.

Confirm the diff really is a regeneration before trusting it: it must move the `# Flux Version:` header **and** carry CRD or schema hunks. If the only changes are `app.kubernetes.io/version` labels and `image:` tags, Renovate did not regenerate — see [Regenerating by hand](#regenerating-by-hand).

The five checks below are what "review" means. They are all answerable from git and the release notes; none require cluster access.

> **Validation passing is not evidence the upgrade is safe.** `flux build` uses the Flux **CLI**, and the CLI and the controllers do not always behave identically — see [check 5](#5-behaviour-changes-in-substitution). CI went green on the v2.9.4 upgrade and Akron's `monitoring` Kustomization broke on merge. The checks below are the real gate.

## The five checks

### 1. Removed APIs

Each release's notes list API versions that reached end-of-life. Check them against what the repo declares:

```bash
git grep -h "apiVersion:.*toolkit.fluxcd.io" base/ sites/ | sort -u
```

This should only ever return GA versions — `helm.toolkit.fluxcd.io/v2`, `kustomize.toolkit.fluxcd.io/v1`, `source.toolkit.fluxcd.io/v1`. If it does, a removal cannot break reconciliation.

A removal can still strand an object created **outside** GitOps, which git cannot see. If the notes remove a notification or image API, confirm nothing is stored at that version:

```bash
kubectl get alerts,providers,receivers -A   # expect: no resources found
```

### 2. Minimum Kubernetes version

Every Flux minor states a supported Kubernetes range, and it moves faster than you would expect — v2.9 dropped support for everything below v1.34. Compare it against the version pinned in `base/system-upgrade-controller/plans.yaml`.

If the floor is above what is pinned, upgrade k3s first and let the Flux PR wait.

### 3. The CI pin

`.github/workflows/validate.yml` pins the Flux CLI so CI builds the way the clusters will. Bump `version:` under the `fluxcd/flux2/action` step **in the same PR as the manifests**.

Renovate will never do this for you: its `flux` manager reads Flux manifests and has no idea a GitHub Actions input exists.

### 4. Your local CLI

Validation step 2 shells out to `flux build`. Running it with an older CLI than the manifests you are validating is a false green.

```bash
brew info fluxcd/tap/flux      # check what stable is before upgrading
brew upgrade fluxcd/tap/flux
flux version --client          # must match the target
```

If brew's stable has already moved past the version you are shipping, install that exact one instead:

```bash
FLUX_VERSION=<x.y.z> curl -s https://fluxcd.io/install.sh | bash
```

### 5. Behaviour changes in substitution

The checks above ask what the release *removed*. This one asks what it made **stricter**, which is the failure mode that actually bit.

Scan the release notes for anything touching `postBuild`, `substitute`, `substituteFrom`, `substituteStrategy` or envsubst. v2.9 shipped "Honor `ks.spec.postBuild.substituteStrategy`", which reads like a small fix and was not:

- **On v2.8.8**, an undefined `${var}` was left alone. Harmless.
- **On v2.9.4**, kustomize-controller substitutes in **strict mode** — one undefined variable fails the entire Kustomization.

Akron carried ~591 Grafana template variables (`${datasource}`, `${ds_prometheus}`) inside dashboard JSON. All harmless for months, all fatal the moment v2.9.4 reconciled:

```
post build failed for 'ConfigMap.v1.[noGrp]/dashboard-node-exporter-full':
envsubst error: variable substitution failed: variable not set (strict mode): "ds_prometheus"
```

**`flux build` does not reproduce this.** The 2.9.4 CLI is lenient about undefined variables while the controller is strict, so validation step 2 exits 0 on manifests that break the cluster. Step 7 (`07-variable-check.sh`) is the only defense, and only because it now scans case-insensitively — the original uppercase-only pattern could not see a lowercase `${ds_prometheus}`.

So when a release touches substitution, verify by reading step 7's output rather than trusting a green run:

```bash
./scripts/validate/03-kustomize-build.sh && ./scripts/validate/07-variable-check.sh
```

Any resource whose `${...}` belongs to the target application rather than to Flux must opt out explicitly, and then Flux never touches it:

```yaml
kustomize.toolkit.fluxcd.io/substitute: disabled
```

Escaping variables one at a time with `$$` is **not** the fix — that was tried in #90 and #107 and left the mechanism in place for the next dashboard to trip over. Worse, once a resource opts out, nothing unescapes `$$` any more, so old escapes become literal.

Then run `./scripts/validate-k3s.sh` and merge.

## Flux CRDs need no manual step

This is the one place Flux differs from every other CRD-bearing upgrade in this repo, and it is easy to get backwards.

[Helm Chart Upgrades with CRD Changes](helm-crd-upgrades.md) exists because **Helm** deliberately never updates CRDs on `helm upgrade` — Traefik and kube-prometheus-stack CRDs have to be applied by hand with `kubectl apply --server-side`.

Flux is not in that category. Its CRDs are ordinary manifests inside `gotk-components.yaml`, reconciled by the `flux-system` Kustomization like anything else, so schema changes apply themselves — **including removals**. The v2.9.4 upgrade deleted three `v1beta2` CRD schemas with no manual step at either site.

So: "new Flux version, do I need to fetch CRDs?" is always no. That question belongs to Helm charts.

## Rollout order

Akron is the canary, and the branch layout enforces it — Akron's `GitRepository` watches `main`, Eastbank's watches `stable`:

1. Merge to `main`. Akron's controllers pick up the new manifests and rolling-restart themselves.
2. Confirm Akron is healthy (below). Reconciliation pauses for ~1–2 minutes during the restart; nothing breaks, work queues up.
3. Run the **Promote to stable** workflow from the Actions tab. Eastbank upgrades.

```bash
flux version      # server-side versions, not just the CLI
flux get all -A   # everything Ready
```

## Both sites must stay identical

Both sites install the same components, so `clusters/akron/flux-system/gotk-components.yaml` and `clusters/eastbank/flux-system/gotk-components.yaml` are byte-identical. Nothing enforces this, so check it after any regeneration:

```bash
diff clusters/akron/flux-system/gotk-components.yaml \
     clusters/eastbank/flux-system/gotk-components.yaml
```

Any output means a botched regeneration — most likely one site written with a different CLI version.

## Regenerating by hand

Only needed when the bump is a **major** release, when Renovate produced a label-only diff, or when the installed component list itself must change (the `# Components:` header at the top of `gotk-components.yaml`).

**Do not use `flux bootstrap` for upgrades.** Bootstrap diffs and re-applies the component manifests and pushes any drift straight to `main`, which the branch ruleset blocks. Bootstrap is for initial setup and sync config changes; `flux install --export` is the tool for upgrades, and it generates manifests locally without contacting a cluster.

1. Read the release's upgrade guide first. Flux publishes breaking-change notes per minor, linked from the releases page.
2. Install the target CLI locally and verify it — check 4 above.
3. Branch from `origin/main` and regenerate **both** sites:
   ```bash
   git fetch origin && git checkout -b flux-upgrade-v<x.y.z> origin/main
   flux install --export > clusters/akron/flux-system/gotk-components.yaml
   flux install --export > clusters/eastbank/flux-system/gotk-components.yaml
   ```
4. Confirm the two files still match, run the four checks, then validate, commit and open a PR as normal.
