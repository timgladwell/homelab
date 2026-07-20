# Runbooks

Operational procedures for tasks that fall outside the normal GitOps flow.

---

## Helm Chart Upgrades with CRD Changes

### Background

Helm (and by extension Flux) never updates CRDs automatically on `helm upgrade`. CRDs are only installed on `helm install`. This is an intentional Helm safety boundary: CRDs are schema definitions for live cluster resources, and updating them incorrectly could corrupt data.

When a Helm chart ships updated CRDs (common on major and some minor version bumps), they must be applied manually **before or alongside** merging the Renovate PR that bumps the chart version.

Flux's `crds: CreateReplace` option on a `HelmRelease` can automate this, but CRD updates are irreversible — Flux cannot roll them back if something goes wrong. The manual approach is retained here intentionally so that upgrades with breaking CRD changes require explicit review.

### General process

1. **Read the chart's upgrade guide** before merging any Renovate PR for a major or minor version bump.
2. **Identify CRD changes** — look for a migration guide, CHANGELOG entry, or `crds/` directory diff between the old and new chart version.
3. **Apply updated CRDs** using server-side apply (safer than client-side for CRDs):
   ```bash
   kubectl apply --server-side --force-conflicts -f <crd-url-or-file>
   ```
   - `--server-side`: the API server computes the merge, which is more correct for complex schemas.
   - `--force-conflicts`: overwrites fields owned by another manager (e.g. a previous `kubectl apply` or Flux), preventing the update from being blocked by field ownership conflicts.
4. **Merge the Renovate PR** — Flux reconciles the `HelmRelease` and runs the equivalent of `helm upgrade` automatically. You do not need to run `helm` commands directly.

### Breaking changes to check beyond CRDs

Not all breaking changes are CRD-related. Also check the chart's changelog for:
- **Values restructuring** — fields moved, renamed, or re-nested under new keys (requires updating `values:` in the `HelmRelease`)
- **Provider or feature renames** — keys that silently have no effect if not updated

---

## Traefik

### Upgrade checklist

For any Traefik Helm chart version bump:

1. **Read the chart release notes** ([traefik-helm-chart releases](https://github.com/traefik/traefik-helm-chart/releases)) for the versions between the old and new chart version. Note any breaking changes — values renames/restructuring, provider renames, default changes.

2. **Cross-reference each breaking change against `infrastructure/homelab/traefik/helmrelease.yaml`** — most chart-level breaking changes don't apply since this repo only sets a small subset of values (resources, service, deployment, ingressRoute, ports, providers, additionalArguments, logs). Grep the changed value paths against the file directly rather than guessing.

3. **Check whether the Traefik app version changed** (`appVersion` in the chart, shown in the release notes). If it's a new minor/major (not just a patch), read the [Traefik migration guide](https://doc.traefik.io/traefik/migration/v3/) for that version.

4. **Check for CRD changes**, regardless of whether the chart or app version moved. Diff the CRD definitions between the old and new Traefik app version:
   ```bash
   diff <(curl -s https://raw.githubusercontent.com/traefik/traefik/v<OLD>/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml) \
        <(curl -s https://raw.githubusercontent.com/traefik/traefik/v<NEW>/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml)
   ```
   If there's a diff, apply the new CRDs per the [general process](#general-process) above before merging.

5. **Gateway API CRDs** — only relevant if using the Gateway API provider, which this cluster does not use.

### Past upgrades

| Chart version | Traefik app version | CRD update? | Notes |
|----|----|----|----|
| v39 → v40 | v3.7 | Yes | New retry middleware options added to CRD provider |
| v40 → v41 | v3.7.4 → v3.7.5 | No (CRDs identical) | `logs.general`/`logs.access` renamed to `log`/`accessLog`; `providers.file.content` string→object (not used here) |

---

## Flux Upgrades

### Patch upgrades (x.y.Z)

Just merge the Renovate PR. Renovate updates the image tags in `gotk-components.yaml` and Flux self-upgrades by reconciling its own manifests. No manual steps required.

### Minor and major upgrades (x.Y.z or X.y.z)

Minor and major releases can change CRD schemas, RBAC rules, and controller manifests in ways that Renovate's tag-only bumps don't capture. The correct approach is to regenerate `gotk-components.yaml` using the Flux CLI, then ship it as a normal PR.

**Do not use `flux bootstrap` for upgrades.** Bootstrap pushes directly to `main`, which branch protection blocks. It is also unnecessary — bootstrap is for initial setup and sync config changes. For controller upgrades, `flux install --export` is the right tool.

#### Process

1. **Read the release upgrade guide** before starting. Each minor/major release publishes breaking-change notes. The v2.7+ guide is linked from the Flux releases page.

2. **Install the target Flux CLI version locally** (no server access needed — `flux install --export` only generates manifests, it does not connect to the cluster):
   ```bash
   # Latest
   brew upgrade fluxcd/tap/flux

   # Specific version
   FLUX_VERSION=2.9.0 curl -s https://fluxcd.io/install.sh | bash
   ```

3. **Verify the CLI version:**
   ```bash
   flux version --client
   ```

4. **Branch from `origin/main` and regenerate the component manifests:**
   ```bash
   git fetch origin && git checkout -b flux-upgrade-v2.x.x origin/main
   flux install --export > clusters/homelab/flux-system/gotk-components.yaml
   ```

5. **Review the diff** — expect CRD, RBAC, and Deployment changes. Cross-reference with the upgrade guide to confirm nothing unexpected.

6. **Run validation, commit, open a PR, and merge as normal.** Flux reconciles `clusters/homelab/flux-system/` and the controllers rolling-restart themselves. There is a brief (~1–2 min) window during the restart where reconciliation is paused; nothing breaks, work queues up.

7. **Confirm the upgrade completed:**
   ```bash
   flux version
   flux get all -A
   ```

---

## Rotating the GitHub PAT for Flux

### Background

Flux authenticates to GitHub via the `flux-system` Secret in the `flux-system` namespace, referenced by the `flux-system` `GitRepository` source (`clusters/homelab/flux-system/gotk-sync.yaml`). It holds a `username`/`password` pair where `password` is the PAT.

**Do not use `flux bootstrap` to rotate the token.** Bootstrap also diffs and re-applies the Flux component manifests and commits/pushes any drift directly to `main`, which branch protection blocks (same reason it's banned for [Flux Upgrades](#flux-upgrades) above). Rotating the token only requires updating the Secret — it's cluster credential state, not GitOps-managed config, so a direct `kubectl apply` is the correct tool here, not a repo change.

### Process

1. **Generate a new PAT** on GitHub with the same scopes as the existing one (`repo` for a classic PAT, or equivalent fine-grained permissions).

2. **Check the existing secret's username field** (skip if you already know it):
   ```bash
   kubectl get secret flux-system -n flux-system -o jsonpath='{.data.username}' | base64 -d
   ```

3. **Patch the secret in place** with the new token, keeping the same username:
   ```bash
   kubectl create secret generic flux-system \
     --namespace flux-system \
     --from-literal=username=<value from step 2> \
     --from-literal=password=<new-token> \
     --dry-run=client -o yaml | kubectl apply -f -
   ```

4. **Force reconciliation and confirm it succeeds:**
   ```bash
   flux reconcile source git flux-system
   flux get sources git
   ```

5. **Revoke the old PAT** on GitHub once reconciliation is confirmed healthy.

---

## Migrating Akron to the Multi-Site Layout

### Background

The multi-site restructure PR renames `clusters/homelab/` to `clusters/akron/` and changes `infrastructure/homelab/` to `infrastructure/core/` + `infrastructure/akron-only/`. Akron's Flux instance is already live, reconciling the *old* path (`./clusters/homelab`) from its in-cluster root `Kustomization` object (named `flux-system`, namespace `flux-system`). That object's `spec.path` is stored in the cluster, not re-read from git on every reconcile — so once this PR merges and `clusters/homelab/` no longer exists in the repo, Akron's root Kustomization will fail to build (path not found) until `spec.path` is updated to `./clusters/akron`.

**Do not run `flux bootstrap` to fix this** — bootstrap pushes directly to `main`, which branch protection blocks (same reason it's banned for [Flux Upgrades](#flux-upgrades) and [PAT rotation](#rotating-the-github-pat-for-flux) above). This is a one-time in-cluster object update, not a GitOps-managed change, so `kubectl apply` against the already-merged file is the correct tool.

### Process

1. **Merge the multi-site restructure PR to `main`** first — `clusters/akron/flux-system/gotk-sync.yaml` (with `path: ./clusters/akron`) must exist in git before the next step.

2. **On the Akron server**, pull the merged `main` and apply the updated root Kustomization/GitRepository directly:
   ```bash
   git pull origin main
   kubectl apply -f clusters/akron/flux-system/gotk-sync.yaml
   ```
   This updates the in-cluster `flux-system` Kustomization's `spec.path` to `./clusters/akron` immediately — do not wait for a reconcile loop to pick it up, it won't (it's still looking at the old, now-missing path until this apply happens).

3. **Confirm reconciliation recovers:**
   ```bash
   flux get kustomizations -A
   flux get sources git
   ```
   Expect `infrastructure`, `infrastructure-akron-only`, `infrastructure-config`, `apps`, `app-config` to all appear (the last four are new/renamed Kustomization names — see `clusters/akron/kustomization.yaml`) and go healthy within a couple of reconcile intervals.

4. **If `infrastructure-akron-only` or `app-config` fail on `dependsOn`,** check `flux get kustomizations -A` for the dependency chain — `apps` now depends on `infrastructure-akron-only` (PodMonitor CRD from kube-prometheus-stack) in addition to `infrastructure` and `infrastructure-config`; this is new as of the restructure.

5. **Verify no DNS disruption** — `infrastructure/core/dns/` content is unchanged from `infrastructure/homelab/dns/` (only the parent directory moved), so PiHole/Unbound should not restart or lose state during this migration. If they do restart, it's the RollingUpdate-safe path (see `feedback_pihole_recreate_strategy` guidance) — not a Recreate outage.

---

## Bootstrapping a New Remote Site (Eastbank / Lottage)

### Background

Eastbank and Lottage are new, previously-bare-metal sites being brought under GitOps for the first time. Unlike the Akron migration above, `flux bootstrap` **is** the correct tool here — it's genuine initial setup, not an upgrade to an already-bootstrapped cluster. The manifests it would generate (`clusters/<site>/flux-system/gotk-components.yaml`, `gotk-sync.yaml`) already exist in the repo from the restructure PR, pre-populated to match; bootstrap should find them already correct and only need to create the GitHub deploy credentials and apply to the new cluster.

Both sites' `gotk-sync.yaml` watch the `stable` branch, not `main` — so bootstrap pushes (if any are needed) go to `stable`, which has no branch protection blocking it. Verify this assumption against the repo's actual branch protection rules before running bootstrap; if `stable` also has protection rules, treat this the same as the Akron case above (`kubectl apply` the pre-committed manifest instead of running bootstrap).

### Process (per site — repeat for Eastbank, then Lottage, only after Akron is confirmed healthy)

1. **Fill in real network values.** `clusters/<site>/cluster-vars.yaml` has `CHANGEME` placeholders for `METALLB_ADDRESS_RANGE`, `METALLB_TRAEFIK_IP`, `METALLB_PIHOLE_IP`, `NODE_IP` (Eastbank only — Lottage has no MetalLB). Replace with that site's actual static IPs before merging.

2. **Generate that site's age keypair** (do this locally, keep the private key off any machine that doesn't need it):
   ```bash
   age-keygen -o <site>.agekey
   age-keygen -y <site>.agekey   # prints the public key
   ```

3. **Replace the `CHANGEME-<site>-age-public-key` placeholder** in `.sops.yaml` with the real public key from step 2, in the same PR as step 1.

4. **Re-encrypt existing shared secrets** (currently only `infrastructure/core/dns/pihole-secret.sops.yaml`) with the new recipient list:
   ```bash
   sops updatekeys infrastructure/core/dns/pihole-secret.sops.yaml
   ```
   Requires the *existing* Akron private key available locally (to decrypt) — this does not need the new site's key yet, only its public key already in `.sops.yaml`.

5. **Merge the PR containing steps 1, 3, 4.**

6. **On the new site's device**, install the site's private key and run bootstrap:
   ```bash
   mkdir -p ~/.config/sops/age
   # copy <site>.agekey content to ~/.config/sops/age/keys.txt (chmod 600)

   flux bootstrap github \
     --owner=timgladwell \
     --repository=homelab \
     --branch=stable \
     --path=clusters/<site> \
     --personal
   ```

7. **Install the `sops-age` secret** in the new cluster (same as `scripts/configure-flux-sops.sh` does for Akron):
   ```bash
   kubectl create secret generic sops-age \
     --namespace=flux-system \
     --from-file=age.agekey=~/.config/sops/age/keys.txt
   ```

8. **Confirm reconciliation:**
   ```bash
   flux get kustomizations -A
   flux get sources git
   ```
   Eastbank should show `infrastructure`, `infrastructure-config`, `app-config`. Lottage should show only `infrastructure`, `app-config` (no MetalLB config layer).

9. **Verify PiHole is actually serving DNS** on the new site's LAN before pointing any client devices at it, and — for Lottage specifically — confirm a backup DNS resolver is configured on the router/LAN *before* the first deploy that touches PiHole/Unbound, since Lottage's `hostNetwork` + `Recreate` strategy means every rollout is a DNS outage window for that site (see `infrastructure/core-overlays/lottage/pihole-hostnetwork-patch.yaml` comment and the `feedback_pihole_recreate_strategy` memory).
