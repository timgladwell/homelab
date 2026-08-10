# Rotating the GitHub PAT for Flux

## Background

Flux authenticates to GitHub via the `flux-system` Secret in the `flux-system` namespace, referenced by the `flux-system` `GitRepository` source (`clusters/<site>/flux-system/gotk-sync.yaml`). It holds a `username`/`password` pair where `password` is the PAT.

**Each site has its own Secret, so rotation is per-site.** Nothing is shared between Akron and Eastbank — repeat the process below against each cluster in turn.

**Do not use `flux bootstrap` to rotate the token.** Bootstrap also diffs and re-applies the Flux component manifests and commits/pushes any drift directly to `main`, which branch protection blocks (same reason it's banned for [Flux Upgrades](flux-upgrades.md) above). Rotating the token only requires updating the Secret — it's cluster credential state, not GitOps-managed config, so a direct `kubectl apply` is the correct tool here, not a repo change.

## Process

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
