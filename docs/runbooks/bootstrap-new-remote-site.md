# Bootstrapping a New Remote Site

## Background

A new site is bare metal being brought under GitOps for the first time, so `flux bootstrap` **is** the correct tool here — it's genuine initial setup, not an upgrade to an already-bootstrapped cluster, where updating the in-cluster `Kustomization` directly is correct instead. The manifests it would generate (`clusters/<site>/flux-system/gotk-components.yaml`, `gotk-sync.yaml`) are committed before bootstrap (step 1 copies them from Eastbank); bootstrap should find them already correct and only need to create the GitHub deploy credentials and apply to the new cluster.

**Akron watches `main`; every other site watches `stable`.** That is the canary order — Akron takes each change first, and remote sites only see it once it is promoted. A new site's `gotk-sync.yaml` therefore points at `stable`. `stable` is moved only by the **Promote to stable** workflow, as a fast-forward to a commit that already exists on `main` — never by a PR, merge or rebase. Its ruleset is `deletion` + `non_fast_forward` + `required_signatures` with no bypass actor ([ADR 0002](../adr/0002-rollout-across-sites.md)), so `stable` can only ever move forward, but **fast-forward pushes are not blocked**. This means `stable` must already contain the site's manifests (promote before this runbook's step 7) for reconciliation to find anything once bootstrap connects.

**The Flux CLI version you bootstrap with must match `app.kubernetes.io/version` in the site's already-committed `gotk-components.yaml` exactly.** A mismatched CLI regenerates different component manifests, which is a diff `flux bootstrap` will try to push to `stable` — and since only non-fast-forward pushes are refused, that push can *succeed*, silently putting content on `stable` that never passed through `main`. The version check is the only thing preventing it.

**Auth mode: `--token-auth`, matching Akron.** Without this flag, `flux bootstrap` defaults to creating an SSH deploy key on the repo and storing it in-cluster — a different auth mechanism than Akron actually uses (see `github-pat-rotation.md`: Akron's `flux-system` Secret holds a `username`/`password` pair where `password` is a PAT). `--token-auth` stores that PAT in-cluster instead of creating a deploy key, keeping every site's auth mechanism consistent. Use a **fine-grained PAT scoped to only the `homelab` repository**, with `Contents: Read-only` and `Administration: Read-only` — read-only is sufficient because the cluster only ever needs to pull; if the CLI version match above holds, bootstrap never needs to push anything, and no site's cluster should ever be writing back to git.

If this is a brand-new device (not just a Flux re-bootstrap on existing hardware), the OS itself needs standing up first — see [Standing Up a New Headless Box](new-box-standup.md).

## Process (only after Akron is confirmed healthy)

1. **Copy Eastbank's directories and fill in real network values.** Copy `sites/eastbank/` to `sites/<site>/` and `clusters/eastbank/` to `clusters/<site>/`, replacing `eastbank` throughout. Drop components Eastbank alone runs (NetworkOptimizer in `apps/`, Unpoller in `monitoring/`) unless this site should run them. Then set this site's static IPs in `clusters/<site>/cluster-vars.yaml`: `METALLB_ADDRESS_RANGE`, `METALLB_TRAEFIK_IP`, `METALLB_PIHOLE_IP`, `NODE_IP`, `LAN_CIDR`, `LAN_GATEWAY`.

   Put this site's dnsmasq records in the copied `sites/<site>/infrastructure/site.conf` (Eastbank's is comments only). Keep the file even if it stays empty: `policy/pihole_dnsmasq_site.rego` fails the build without it, because a site with no file is indistinguishable from a site that wants no DNS records.

2. **Generate that site's age keypair** (do this locally, keep the private key off any machine that doesn't need it):
   ```bash
   age-keygen -o <site>.agekey
   age-keygen -y <site>.agekey   # prints the public key
   ```

3. **Add a creation rule to `.sops.yaml`** for `^sites/<site>/.*secret\.sops\.yaml$` with the public key from step 2 — copy Eastbank's rule. Same PR as step 1.

4. **Re-key the copied secrets to this site, then set its real values.** Every secret is scoped to one site's directory, so there is nothing shared to re-encrypt. The copies are still encrypted to Eastbank's key: `sops` applies `.sops.yaml` only when a file is created or explicitly re-keyed, not on `edit`, so `updatekeys` comes first:
   ```bash
   find sites/<site> -name '*secret*.sops.yaml' -exec sops updatekeys -y {} \;
   ./scripts/secrets-helper.sh edit sites/<site>/infrastructure/cloudflare-secret.sops.yaml
   ./scripts/secrets-helper.sh edit sites/<site>/infrastructure/pihole-secret.sops.yaml
   ./scripts/secrets-helper.sh edit sites/<site>/apps/pihole-secret.sops.yaml
   ```
   Requires Eastbank's private key locally to decrypt each copy once. Confirm no copy still names Eastbank's key — validation only checks that secrets are encrypted, not to whom, so a miss surfaces as a Flux decryption failure on the new cluster:
   ```bash
   grep -l "$(grep -A1 'sites/eastbank' .sops.yaml | awk '/age:/{print $2}')" -r sites/<site>   # expect no output
   ```

   **The two `pihole-secret` files must hold the same password.** They are separate files only because Secrets do not cross namespaces — Pi-hole and `pihole-sync` read the one in `dns`, the landing page reads the one in `landing`. Nothing validates that they agree; a mismatch shows up as the landing page failing every call while Pi-hole itself is healthy.

5. **Merge the PR containing steps 1, 3, 4.**

6. **Confirm the device's hostname is its FQDN before installing k3s** (`hostnamectl --static`). K3s takes its node name from the hostname and there is no `--node-name` in the install below, so a short hostname here becomes a wrongly-named node that must be renamed afterwards — see [Renaming the K3s Node](node-rename.md). Fix it first with:
   ```bash
   sudo ./scripts/set-node-identity.sh k3s01.<site>.internal.zerpzorp.com
   ```

7. **On the new site's device, install k3s.** Traefik and MetalLB are deployed by this repo, so disable k3s's built-in equivalents to avoid conflicts. Also set `K3S_KUBECONFIG_MODE=644` — without it, `/etc/rancher/k3s/k3s.yaml` is written `600` root-only and `kubectl`/`flux` fail with `permission denied` for the non-root user:
   ```bash
   curl -sfL https://get.k3s.io | K3S_KUBECONFIG_MODE=644 sh -s - --disable traefik --disable servicelb
   echo $KUBECONFIG   # dotfiles may already export this; only add it yourself if empty
   ```
   Raspberry Pi OS doesn't enable the memory cgroup by default, which k3s requires — if the install fails with `Failed to find memory cgroup` / `k3s.service` won't start, enable it and reboot:
   ```bash
   sudo sed -i 's/$/ cgroup_memory=1 cgroup_enable=memory/' /boot/firmware/cmdline.txt
   cat /boot/firmware/cmdline.txt   # must stay a single line — appended flags, no newline
   sudo reboot
   ```
   Then re-run the k3s install command above.

   **Raise the inotify instance limit before the node carries a full workload:**
   ```bash
   echo 'fs.inotify.max_user_instances = 1024' | sudo tee /etc/sysctl.d/90-inotify.conf
   sudo sysctl --system
   cat /proc/sys/fs/inotify/max_user_instances   # must read 1024
   ```
   The kernel default is 128 and the limit is **per UID, not per process** — k3s-server,
   systemd and every containerd-shim all run as root and draw from the same pool.
   `k3s-server` alone holds ~54. Once root exhausts it, whichever process starts next
   fails to create a watcher, and the symptom lands wherever the race happened to be
   lost rather than on the cause — see #160.
   If k3s was already installed without `K3S_KUBECONFIG_MODE` (installer logs "No change detected so skipping service start"), re-run the install command with the env var set, then `sudo systemctl restart k3s` to pick it up.

8. **Generate a fine-grained PAT** scoped to only the `homelab` repository, with `Contents: Read-only` and `Administration: Read-only` (see Background — this stays in-cluster, so keep it read-only).

9. **On the new site's device, install the Flux CLI at the version pinned in the repo, the site's private key, and run bootstrap.** Env vars don't survive a pipe into `sudo`, so use `sudo env` rather than prefixing the `curl` command:
   ```bash
   grep 'app.kubernetes.io/version' clusters/<site>/flux-system/gotk-components.yaml | head -1
   curl -s https://fluxcd.io/install.sh -o /tmp/flux-install.sh
   sudo env FLUX_VERSION=<version from above, no leading v> bash /tmp/flux-install.sh
   flux --version   # confirm it matches

   mkdir -p ~/.config/sops/age
   # copy <site>.agekey content to ~/.config/sops/age/keys.txt (chmod 600)

   flux bootstrap github \
     --owner=timgladwell \
     --repository=homelab \
     --branch=stable \
     --path=clusters/<site> \
     --personal \
     --token-auth \
     --version=<version from above, WITH leading v — e.g. v2.8.8>
   ```
   Paste the PAT from step 8 when prompted. Bootstrap should report the component manifests already match and skip straight to applying and creating the `flux-system` Secret — no commit, no push, no deploy key.

10. **Install the `sops-age` secret** in the new cluster (same as `scripts/configure-flux-sops.sh` does for Akron):
   ```bash
   kubectl create secret generic sops-age \
     --namespace=flux-system \
     --from-file=age.agekey=$HOME/.config/sops/age/keys.txt
   ```

11. **Confirm reconciliation:**
   ```bash
   flux get kustomizations -A
   flux get sources git
   ```
   Every layer in `clusters/<site>/` should be `Ready`: `infrastructure`, `infrastructure-config`, `dns-config`, `monitoring`, `apps`.

12. **Verify PiHole is actually serving DNS** on the new site's LAN before pointing any client devices at it.

13. **Verify the site's metrics reach Akron.** In Grafana at Akron, `up{site="<site>"}` should return the new site's targets within a few minutes. Nothing alerts on a site that never started sending.
