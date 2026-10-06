# 4. How the repository is layered across sites

- **Status:** Accepted
- **Date:** 2026-08-06
- **Deciders:** Tim Gladwell

## Changelog

| Date | By | Description |
| --- | --- | --- |
| 2026-08-06 | Tim | `base/` + `sites/` + `clusters/` layering replaces diffing sites against Akron |
| 2026-10-06 | Tim and Claude | Written up as an ADR from CLAUDE.md (#402) |

## Context

Several independent single-node K3s sites share this repository, using Flux's
standard multi-cluster monorepo pattern: no federation, no shared control plane.
Sites run overlapping but different sets of components, and differ in their own
values (IPs, domains, keys). Changes must be reviewable as one diff, and adding a
site or a component must not mean editing every other site.

## Decision

Three layers, composed many-to-many:

| Layer | Directory | Contains |
|---|---|---|
| Component | `base/<component>/` | Site-agnostic definitions. No secrets, no site values; anything that differs per site is a `${VAR}` placeholder. |
| Site | `sites/<site>/<layer>/` | This site's secrets, patches, and the list of `base/` components it wants. One subdirectory per Flux Kustomization. |
| Entry point | `clusters/<site>/` | Flux bootstrap manifests, `cluster-vars.yaml`, and the Flux `Kustomization` objects pointing at `sites/<site>/<layer>/`. |

**`base/` never contains anything site-specific.** A site opts into a component
by listing it, and opts out by not listing it. A `$patch: delete` against `base/`
means the layering is wrong.

**`clusters/<site>/` is what Flux needs to reconcile this site; `sites/<site>/` is
what the site deploys.** `cluster-vars.yaml` is identity, not deployment: it is
reconciled by `flux-system` before any layer and is an input to every layer via
`postBuild.substituteFrom`, so no layer can supply it.

**Variables come from two ConfigMaps**, substituted into every layer,
`network-vars` first:

| ConfigMap | File | Holds |
|---|---|---|
| `network-vars` | `clusters/common/network-vars.yaml` | What the estate is: values identical at every site, including how one site reaches another. |
| `cluster-vars` | `clusters/<site>/cluster-vars.yaml` | What this site is: values that differ per site. |

A cross-site value has to live in `common/`, because a site cannot read another
site's `cluster-vars`.

**Flux Kustomizations are grouped by reconcile semantics, not by namespace** —
different `dependsOn`, SOPS config, timeout or `force`. A component needing CRDs
that another component installs goes in a layer that `dependsOn` the installing
one (`infrastructure-config` after `infrastructure`).

**A site's Secret is always a whole file**, never a Kustomize patch over a shared
one, and each site's secrets are encrypted only to that site's age key
(`.sops.yaml` scopes keys by `sites/<site>/**`). There are no shared secrets.

## Consequences

**Positive**

- Adding a site is copying an existing site's `sites/` and `clusters/`
  directories ([runbook](../runbooks/bootstrap-new-remote-site.md)). Adding a
  component is one line per site that wants it.
- Validation discovers sites and layers from the directories and the Flux
  `Kustomization` objects, so nothing is hand-listed.

**Negative and risks**

- A Flux `Kustomization`'s `metadata.name` is load-bearing: renaming one prunes
  everything it owned, PVCs included. Moving a resource between Kustomizations
  races a prune against a create. See
  [Renaming or moving across Flux Kustomizations](../runbooks/flux-kustomization-changes.md).
- Plain `kustomize build` and `flux build --dry-run` do not substitute
  variables, so validation does its own substitution to see what is applied.
- Fields identical across sites are repeated in each site's Secret, because
  SOPS metadata describes a whole document and field-level merges of separately
  encrypted files cannot be decrypted.

## Alternatives considered

- **`cluster-vars.yaml` under `sites/<site>/`.** The obvious home, since it is
  site-specific. Rejected: it is an input to every layer rather than an output of
  one, and `clusters/<site>/kustomization.yaml` could only reach it through a
  top-level `sites/<site>/kustomization.yaml` shaped unlike every per-layer one.
- **Diffing each site against Akron.** The layout before this decision: the
  shared directory was really Akron's config, Akron's Pi-hole secret included,
  and every other site was a diff against it — a `$patch: delete` on the secret,
  a nested kustomization to sequence that delete, and a duplicate secret
  restating every field. Akron alone bypassed the overlay tree, so the sites
  were not comparable.
- **A Kustomize patch per site over a shared Secret.** Builds cleanly and fails
  at decryption on the cluster.
