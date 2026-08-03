# Traefik

## Upgrade checklist

For any Traefik Helm chart version bump:

1. **Read the chart release notes** ([traefik-helm-chart releases](https://github.com/traefik/traefik-helm-chart/releases)) for the versions between the old and new chart version. Note any breaking changes — values renames/restructuring, provider renames, default changes.

2. **Cross-reference each breaking change against `infrastructure/homelab/traefik/helmrelease.yaml`** — most chart-level breaking changes don't apply since this repo only sets a small subset of values (resources, service, deployment, ingressRoute, ports, providers, additionalArguments, logs). Grep the changed value paths against the file directly rather than guessing.

3. **Check whether the Traefik app version changed** (`appVersion` in the chart, shown in the release notes). If it's a new minor/major (not just a patch), read the [Traefik migration guide](https://doc.traefik.io/traefik/migration/v3/) for that version.

4. **Check for CRD changes**, regardless of whether the chart or app version moved. Diff the CRD definitions between the old and new Traefik app version:
   ```bash
   diff <(curl -s https://raw.githubusercontent.com/traefik/traefik/v<OLD>/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml) \
        <(curl -s https://raw.githubusercontent.com/traefik/traefik/v<NEW>/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml)
   ```
   If there's a diff, apply the new CRDs per the [general process](helm-crd-upgrades.md#general-process) before merging.

5. **Gateway API CRDs** — only relevant if using the Gateway API provider, which this cluster does not use.

## Past upgrades

| Chart version | Traefik app version | CRD update? | Notes |
|----|----|----|----|
| v39 → v40 | v3.7 | Yes | New retry middleware options added to CRD provider |
| v40 → v41 | v3.7.4 → v3.7.5 | No (CRDs identical) | `logs.general`/`logs.access` renamed to `log`/`accessLog`; `providers.file.content` string→object (not used here) |
