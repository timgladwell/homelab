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

For any Traefik Helm chart version bump, check:

1. [traefik-helm-chart releases](https://github.com/traefik/traefik-helm-chart/releases) for chart-level breaking changes
2. [Traefik migration guide](https://doc.traefik.io/traefik/migration/v3/) for the corresponding Traefik app version

### v39 → v40 (Traefik app v3.7)

**CRD update required** — the Kubernetes CRD provider was updated to support new retry middleware options:

```bash
kubectl apply --server-side --force-conflicts \
  -f https://raw.githubusercontent.com/traefik/traefik/v3.7/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml
```

**Chart-level breaking changes** (neither affects this repo's config):

| PR | Change | Impact |
|----|--------|--------|
| [#1686](https://github.com/traefik/traefik-helm-chart/pull/1686) | `service.loadBalancerSourceRanges`, `externalIPs`, `ipFamilyPolicy`, `ipFamilies`, `externalTrafficPolicy` moved under `service.spec` | Not affected — we only use `service.annotations` |
| [#1714](https://github.com/traefik/traefik-helm-chart/pull/1714) | `providers.kubernetesIngressNginx` renamed to `providers.kubernetesIngressNGINX` | Not affected — we don't use that provider |

**Gateway API CRDs** — only needed if using the Gateway API provider, which this cluster does not use.

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
