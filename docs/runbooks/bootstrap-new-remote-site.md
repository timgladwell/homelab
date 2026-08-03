# Bootstrapping a New Remote Site (Eastbank / Lottage)

## Background

Eastbank and Lottage are new, previously-bare-metal sites being brought under GitOps for the first time. Unlike the [Akron migration](akron-multisite-migration.md), `flux bootstrap` **is** the correct tool here — it's genuine initial setup, not an upgrade to an already-bootstrapped cluster. The manifests it would generate (`clusters/<site>/flux-system/gotk-components.yaml`, `gotk-sync.yaml`) already exist in the repo from the restructure PR, pre-populated to match; bootstrap should find them already correct and only need to create the GitHub deploy credentials and apply to the new cluster.

Both sites' `gotk-sync.yaml` watch the `stable` branch, not `main`. `stable` is protected by a GitHub ruleset (no direct pushes, "Rebase and merge" only) — any content promoted to `stable` must go through a PR from `main`. `flux bootstrap` itself doesn't push to `stable` (the `gotk-sync.yaml`/`gotk-components.yaml` it would generate are already committed), so this doesn't block bootstrap directly, but it means `stable` must already contain the site's manifests (via a promotion PR, merged before this runbook's step 6) for reconciliation to find anything once bootstrap connects.

If this is a brand-new device (not just a Flux re-bootstrap on existing hardware), the OS itself needs standing up first — see [Standing Up a New Headless Box](new-box-standup.md).

## Process (per site — repeat for Eastbank, then Lottage, only after Akron is confirmed healthy)

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
