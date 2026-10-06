# Adding or Removing a Component, Layer or Variable

How a change lands in the layering of [ADR 0004](../adr/0004-repository-layering.md)
without breaking a site. For moving something that already exists between Flux
Kustomizations, use
[Renaming or Moving Across Flux Kustomizations](flux-kustomization-changes.md)
instead.

## Pre-checks

1. **Shared or one site?** Shared goes in `base/<component>/`; one site's goes
   in `sites/<site>/<layer>/`. Nothing site-specific — values, secrets, a
   site's records — goes in `base/`.
2. **Which layer?** Layers are grouped by reconcile semantics, not namespace:
   - plain resources → `infrastructure/`;
   - **custom resources whose CRDs another component installs** →
     `infrastructure-config/`, which `dependsOn: infrastructure`. A CR shipped in
     the same Kustomization as the HelmRelease that installs its CRD works on a
     cluster that already has it and fails on a fresh one with
     `no matches for kind`. Validation step 11 catches this. This is why Traefik
     CRs live in `base/traefik-routes/` and cert-manager's in
     `base/cert-manager-config/`, apart from their HelmReleases.
3. **Does it pin an image?** Its directory needs a `.github/dependabot.yml`
   entry (step 9 enforces it). A version pinned anywhere else needs a
   `# renovate:` comment matched by a `customManagers` entry in `renovate.json`,
   or Renovate silently never bumps it.
4. **Does it read generated config through a `subPath` mount or an env var?**
   Those are read only at pod start, so the config needs a hash-suffixed
   generator (or a chart checksum annotation) or edits never take effect. If the
   chart mounts it by a values field, a `configurations:` entry can teach
   Kustomize's nameReference that field — see
   `sites/eastbank/monitoring/unpoller/kustomizeconfig.yaml`. Secrets still read
   through env vars without this are #415.
5. **Does it change Helm values?** Only validation step 15 checks them, and only
   for render errors and schema violations — a real key with the wrong effect
   renders cleanly. Render and read the output:
   `flate build hr -p clusters/<site>`. A setting whose silent loss would cost
   something earns a rule in `policy/rendered_chart_settings.rego`, which asserts
   it is present in the rendered objects.

## Steps

**A shared component:**

1. Create `base/<component>/` with a `kustomization.yaml`. Use `${VAR}` for
   anything site-specific.
2. Add `- ../../../base/<component>` to each `sites/<site>/<layer>/kustomization.yaml`
   that should get it. A site opts out by not listing it; there is no
   delete-patch pattern.

**One site's component:** create it under `sites/<site>/<layer>/` and list it in
that layer's `kustomization.yaml`.

**A per-site Secret for a component:** a whole SOPS-encrypted file in
`sites/<site>/<layer>/`, listed beside the base component, holding every field
the site needs — never a patch over another site's. Only actual secrets: config
that merely feels private (records, hostnames, ranges) belongs in a ConfigMap or
`cluster-vars`, where it can be reviewed and policy-checked.

**A per-site config file merged into a base ConfigMap** (as `pihole-sync` does
for its client list): keep the global part in `base/`, and merge the site's part
from the site layer, because a `configMapGenerator` cannot read files outside its
own root:

```yaml
configMapGenerator:
  - name: <same-name-as-base>
    behavior: merge
    files:
      - <site-specific>.yaml
```

Back it with a conftest policy asserting the merged key exists
(`policy/pihole_sync_clients.rego`): an absent site file usually reads as
"desired state is empty", which is a delete.

**A web UI:** an `IngressRoute` in the pattern of
`base/traefik-routes/pihole-ingressroute.yaml` is the whole job — TLS and DNS
come from the wildcard. A host *not* behind Traefik needs a dnsmasq override, or
the wildcard swallows it: in `base/dns/dnsmasq-base.conf` if it is all variables,
otherwise in `sites/<site>/infrastructure/site.conf`.

**A variable:** decide its owner first — identical at every site means
`clusters/common/network-vars.yaml`; different per site means every
`clusters/<site>/cluster-vars.yaml` that reconciles a consumer. Add it before, or
in the same PR as, the manifest that uses it (#300). Grep before adding:
nothing reports an unused variable.

**A new top-level Flux Kustomization** (only for different `dependsOn`, SOPS
config or interval — rare):

1. Create `sites/<site>/<layer>/` with a `kustomization.yaml`.
2. Add the Flux `Kustomization` object to `clusters/<site>/` with
   `path: ./sites/<site>/<layer>`, and list it in
   `clusters/<site>/kustomization.yaml`.

**Removing:**

- **A component:** remove its line from the layer's `kustomization.yaml`;
  `prune: true` deletes its resources. **Check what they own first** — pruning
  cascades to PVCs.
- **A top-level Kustomization:** remove its file from `clusters/<site>/` and its
  entry in `clusters/<site>/kustomization.yaml`.

Validation discovers sites and layers on its own; none of these needs a change to
`scripts/`.

## Post-checks

1. `./scripts/validate-k3s.sh` passes.
2. The PR's rendered-diff comment shows only the objects you expected.
3. After merge, at Akron: `flux get kustomizations -A` is `Ready`, and the new
   workload's pods are `Running` with their desired replica count.
