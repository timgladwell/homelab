# Renaming the K3s Node

Changes a node's Linux hostname and K3s node name to its FQDN, per
[the naming convention](../naming-convention.md). Iteration 1 of #228.

**This destroys every PersistentVolume on the node.** That is the intended
outcome here, not an accident — see Trap 1. Do not run this on a cluster whose
data you want to keep without reading that section first.

## When to use this, and what comes first

This is for renaming a node that already exists. It is **not**
[Standing Up a New Headless Box](new-box-standup.md) — that runbook is for a
flash or re-flash, where the FQDN is set in the cloud-init seed and no rename
is ever needed. Do not reimage a working node to rename it.

Sequence:

1. **Merge the PR** carrying the MetalLB rename and this runbook. Akron watches
   `main`, so the `IPAddressPool`/`L2Advertisement` rename reconciles there
   within a couple of minutes and needs no action on the node. Confirm it landed
   and Traefik and PiHole still hold their IPs *before* touching the hostname —
   two changes in flight at once makes any failure ambiguous.
2. **Get the script onto the node.** It runs on the host, not in the cluster:
   ```bash
   # if the repo is already cloned on the node
   git -C ~/homelab pull

   # otherwise, from your dev machine
   scp scripts/set-node-identity.sh tim@<node-ip>:~
   ```
3. **Run the procedure below**, Akron first.
4. **Promote to `stable`** once Akron is verified, then repeat on Eastbank.
   Eastbank does not see the MetalLB rename until that promotion.

## Background

K3s is installed here without `--node-name`, so the node name is simply the
Linux hostname. Renaming the host and restarting K3s therefore registers a
*new* Node object; the old one lingers until deleted.

| | Akron | Eastbank |
|---|---|---|
| Hostname and node name | `k3s01.akron.internal.zerpzorp.com` | `k3s01.eastbank.internal.zerpzorp.com` |

Names stay on `home.arpa` throughout — this settles the node's identity without
touching the DNS zone, so a failure here is one machine rather than one machine
plus every hostname.

## Trap 1 — PersistentVolumes do not survive

local-path PVs carry a `nodeAffinity` pinned to the node name, and
`PersistentVolume.spec.nodeAffinity` is **immutable**. After the rename every PV
references a node that no longer exists, goes `Released`, and can never rebind.
There is no in-place fix; the options are PV surgery or deletion.

We delete. Per #228, blanking state is acceptable for this phase:

- **Prometheus and Loki** restart with no history. Note the date — future-you
  will otherwise debug the gap as a collection fault.
- **PiHole** rebuilds gravity on the next `dns-config` sync job.
- **Grafana dashboards** are provisioned from ConfigMaps in git and are
  unaffected.

## Trap 2 — cloud-init rewrites /etc/hosts on every boot

cloud-init is not first-boot-only. Its `set_hostname` and `update_etc_hosts`
modules are both `PER_ALWAYS`, so they run on every boot.

`set_hostname` is mostly harmless: it records what it last set in
`/var/lib/cloud/data/previous-hostname`, and skips when the live hostname
differs, treating that as a deliberate manual change.

`update_etc_hosts` has no such guard. With `manage_etc_hosts: true` in the seed
it regenerates `/etc/hosts` from a template using the fqdn from the *datasource
config* — the seed's original name — not from the live hostname. The result is
a split: `hostnamectl` reports the new name while `/etc/hosts` points
`127.0.1.1` at the old one, which is harder to spot than a clean revert.

`scripts/set-node-identity.sh` disables cloud-init outright
(`/etc/cloud/cloud-init.disabled`) rather than pinning `preserve_hostname` and
`manage_etc_hosts`. One file instead of two keys, and it covers whatever other
`PER_ALWAYS` module nobody has audited rather than only the two known to bite.
Nothing is lost: cloud-init's job here was first-boot provisioning — user, SSH,
timezone, hostname — which is long finished by the time a node is renamed. A
reflash replaces the root filesystem, so the flag cannot suppress a real
re-provision.

The verification step below reboots specifically to prove it.

## Trap 3 — DNS during the restart

The node resolves through its own PiHole VIP, which is down while K3s restarts.
This is survivable only because of the public-resolver fallback added in #229
(`<site-pihole-ip> 1.1.1.1 1.0.0.1`). Confirm `/etc/resolv.conf` has all three
entries before starting, or the box loses name resolution mid-procedure.

**SSH to the node by IP for this whole procedure**, not by hostname — the
node's IP is static, and the name you would otherwise use is the thing being
changed.

## Procedure

Akron first, per the repo's normal canary order. Eastbank only after Akron is
verified healthy.

1. **Record the current state**, so you can tell what changed (SSH in by IP):
   ```bash
   kubectl get nodes -o wide
   kubectl get pv,pvc -A
   kubectl get svc -A -o wide | grep LoadBalancer
   ```

2. **Confirm the DNS fallback is in place** (Trap 3):
   ```bash
   cat /etc/resolv.conf     # PiHole VIP, then 1.1.1.1, 1.0.0.1
   ```

3. **Set the new identity:**
   ```bash
   sudo ./scripts/set-node-identity.sh k3s01.<site>.internal.zerpzorp.com
   ```

4. **Restart K3s** to re-register under the new name:
   ```bash
   sudo systemctl restart k3s
   kubectl get nodes -o wide     # both names appear; the new one becomes Ready
   ```

   No cordon or drain. On a single-node cluster draining only makes every pod
   `Pending` with nowhere to go, and the restart stops them anyway — the state
   they would be protecting is the state Trap 1 deletes.

5. **Delete the old Node object:**
   ```bash
   kubectl delete node <old-name>
   ```

6. **Delete the orphaned storage.** Every PV is `Released` at this point:
   ```bash
   kubectl get pv | grep -v Bound
   kubectl delete pvc --all -n monitoring
   kubectl delete pvc --all -n dns
   kubectl get pv -o name | xargs -r kubectl delete
   ```
   Flux and the Prometheus operator recreate the PVCs on the next reconcile, and
   local-path provisions fresh empty volumes.

7. **Reclaim the disk.** Deleting the PV does not always remove its directory,
   and these are the largest things on the node:
   ```bash
   sudo du -sh /var/lib/rancher/k3s/storage/* | sort -h | tail
   # remove directories with no matching PV
   ```

8. **Force a reconcile** rather than waiting out the interval:
   ```bash
   flux reconcile kustomization infrastructure --with-source
   flux get kustomizations
   ```

## Verification

```bash
kubectl get nodes -o wide          # only the new FQDN, Ready, no old entry
kubectl get pv,pvc -A              # all Bound; nothing Released or Pending
kubectl get pods -A                # everything Running
kubectl -n metallb-system get ipaddresspool,l2advertisement
```

- The MetalLB objects are named `lan` (renamed from `homelab-pool` / `homelab-l2`
  in the same iteration). Traefik and PiHole must still hold their original
  LoadBalancer IPs — those are pinned by
  `metallb.universe.tf/loadBalancerIPs`, so a changed IP means the annotation is
  not being honoured, not that the pool rename reassigned anything.
- **From a client, not the node:** confirm DNS still resolves. The PiHole
  LoadBalancer IP must not have moved.
- **Reboot the node, then re-check both `hostnamectl --static` and
  `/etc/hosts`.** This is the only test that proves Trap 2 is handled, and
  checking only the hostname misses the actual failure mode — `/etc/hosts` is
  what cloud-init rewrites. Skipping this defers the break to whenever the box
  next restarts on its own.

Expect Prometheus series to restart from empty. **Record the date here when you
run this:**

| Site | Renamed on |
|---|---|
| Akron | |
| Eastbank | |
