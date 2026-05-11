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
