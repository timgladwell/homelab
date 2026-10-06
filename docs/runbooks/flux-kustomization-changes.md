# Renaming or Moving Across Flux Kustomizations

For any change that renames a Flux `Kustomization`, moves a resource from one
Flux `Kustomization` to another, or renames a resource guarded by a uniqueness
webhook. All three are safe in git and dangerous on the cluster, and **no
rendered-manifest diff can show the danger**: the YAML is identical either way,
and the risk lives in Flux's inventory state.

Changing a Flux Kustomization's `spec.path` is safe and does not need this page.

## Pre-checks

1. **Which of these is the change?**
   - **Renaming a Flux `Kustomization`** (`metadata.name` in `clusters/<site>/*.yaml`).
     `flux-system` prunes the old name and cascade-deletes everything it owned,
     **PVCs included**. Avoid it; a name that no longer fits is cheaper than the
     data. The `monitoring` Kustomization was once called
     `infrastructure-akron-only` for exactly this reason.
   - **Moving a resource across a Kustomization boundary** — a component's
     directory listed by a different `sites/<site>/<layer>/kustomization.yaml`.
     The source Kustomization's inventory still remembers the resource, so on its
     next reconcile it prunes it, racing the destination creating it. For a
     `Namespace`, the prune deletes everything inside, PVCs included. This wiped
     Prometheus and Loki history once.
   - **Renaming a resource whose webhook rejects duplicates** (an overlapping
     CIDR, a duplicate hostname, a claimed port). Flux applies before it prunes,
     so the new object meets the old one, the webhook rejects it, the prune
     never runs, and every later reconcile fails the same way. It does not
     self-heal. Hit renaming MetalLB's `IPAddressPool` (#230). Pinning the value
     elsewhere protects the *assignment*, not the *apply*.

2. **What is at stake?** List what the affected Kustomization or resource owns:

   ```bash
   flux tree kustomization <name> -n flux-system
   kubectl get pvc -A
   ```

   A `Namespace` or anything PVC-backed means data loss unless you choose
   otherwise below.

## Steps

**Rename or move with stateful resources** — pick one, in the PR description:

- **Accept the loss** explicitly, if the data is stateless or not valuable.
- **Disable pruning for the move:** set `prune: false` on the source
  Kustomization in the PR that moves the resource; restore `prune: true` in a
  follow-up once the source has reconciled without the resource (its inventory
  no longer lists it — `flux tree kustomization <source>`).
- **Sequence the merge** so the source reconciles and drops the resource from
  its inventory before the destination's first apply, rather than letting both
  race.

**Rename guarded by a webhook:**

1. Merge the rename. The destination Kustomization goes `False` and stays there.
2. Delete the old objects by hand, dependents first (for MetalLB: the
   `L2Advertisement` before the `IPAddressPool`).
3. `flux reconcile kustomization <name> --with-source`.

## Post-checks

```bash
flux get kustomizations -A          # every Kustomization Ready
kubectl get pvc -A                  # the PVCs you meant to keep are Bound, not new
```

A PVC with a fresh `AGE` where you expected an old one means the data went.
