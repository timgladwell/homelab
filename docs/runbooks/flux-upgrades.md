# Flux Upgrades

## Patch upgrades (x.y.Z)

Just merge the Renovate PR. Renovate updates the image tags in `gotk-components.yaml` and Flux self-upgrades by reconciling its own manifests. No manual steps required.

## Minor and major upgrades (x.Y.z or X.y.z)

Minor and major releases can change CRD schemas, RBAC rules, and controller manifests in ways that Renovate's tag-only bumps don't capture. The correct approach is to regenerate `gotk-components.yaml` using the Flux CLI, then ship it as a normal PR.

**Do not use `flux bootstrap` for upgrades.** Bootstrap pushes directly to `main`, which branch protection blocks. It is also unnecessary — bootstrap is for initial setup and sync config changes. For controller upgrades, `flux install --export` is the right tool.

### Process

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
