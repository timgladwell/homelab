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
   `main`, so it reaches the cluster within a couple of minutes.
2. **Finish the MetalLB rename by hand.** It does *not* apply on its own —
   `infrastructure-config` goes `False` and stays there. See
   [Trap 0](#trap-0--the-metallb-rename-deadlocks-flux).
   Get `infrastructure-config` back to `True`, with Traefik and PiHole still on
   their original IPs, *before* touching the hostname — two changes in flight at
   once makes any failure ambiguous.
3. **Get the script onto the node.** It runs on the host, not in the cluster:
   ```bash
   # if the repo is already cloned on the node
   git -C ~/homelab pull

   # otherwise, from your dev machine
   scp scripts/set-node-identity.sh tim@<node-ip>:~
   ```
4. **Run the procedure below**, Akron first.
5. **Promote to `stable`** once Akron is verified, then repeat on Eastbank.
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

## Trap 0 — the MetalLB rename deadlocks Flux

Renaming `homelab-pool`/`homelab-l2` to `lan` is not self-applying. Flux applies
before it prunes, so both pools exist at apply time and MetalLB's validating
webhook rejects the new one:

```
IPAddressPool/metallb-system/lan dry-run failed (Forbidden): admission webhook
"ipaddresspoolvalidationwebhook.metallb.io" denied the request: CIDR
"10.6.1.10/31" in pool "lan" overlaps with already defined CIDR "10.6.1.10/31"
```

The apply fails, so the prune never removes `homelab-pool`, so the next
reconcile fails the same way. `infrastructure-config` sits `False` forever and
**every other change in that Kustomization is blocked behind it** — this is not
a cosmetic warning.

Delete the old objects by hand, dependents first:

```bash
kubectl -n metallb-system get ipaddresspool,l2advertisement

kubectl -n metallb-system delete l2advertisement homelab-l2
kubectl -n metallb-system delete ipaddresspool homelab-pool

flux reconcile kustomization infrastructure-config --with-source
```

`L2Advertisement` goes first because it references the pool by name.

Between the delete and the reconcile there is no L2 advertisement, so ARP for
the PiHole and Traefik VIPs goes unanswered — a real, brief DNS outage for LAN
clients. Run the reconcile immediately rather than waiting out the interval.
The node itself keeps resolving via the `1.1.1.1` fallback from #229.

Verify before continuing:

```bash
kubectl -n metallb-system get ipaddresspool,l2advertisement   # only lan / lan
flux get kustomizations                                        # infrastructure-config True
kubectl get svc -A | grep LoadBalancer                         # IPs unchanged
```

The Services pin their addresses with `metallb.universe.tf/loadBalancerIPs`, so
they reclaim the same IPs from the new pool. That pinning is what makes the
*assignment* safe; it does nothing for the *apply*, which is why this trap
exists at all.

## Trap 1 — PersistentVolumes do not survive

local-path PVs carry a `nodeAffinity` pinned to the node name, and
`PersistentVolume.spec.nodeAffinity` is **immutable**. After the rename every PV
references a node that no longer exists. There is no in-place fix; the volume
has to be replaced.

**The symptom is not what you would expect.** An already-bound PV does *not* go
`Released` — `kubectl get pv | grep -v Bound` comes back empty and everything
looks fine. `nodeAffinity` constrains *scheduling*, not binding, so the failure
surfaces one layer up: pods stuck `Pending` with
`node(s) had volume node affinity conflict`. Check pods, not PVs.

**Delete the PVC, never the PV.** `reclaimPolicy: Delete` means the PV goes with
its claim. Deleting PVs directly races the controllers, which recreate PVCs
within seconds — local-path then provisions replacements pinned to the *new*
node name, and a blanket `kubectl get pv -o name | xargs kubectl delete` wipes
those healthy replacements instead of the stale originals. They wedge in
`Terminating` behind the `pv-protection` finalizer, cannot be un-deleted, and
their new PVCs have to be deleted as well to clear them. This happened during
Akron's rename on 2026-08-22.

Per #228, blanking state is acceptable for this phase:

- **Prometheus and Loki** restart with no history. Note the date — future-you
  will otherwise debug the gap as a collection fault.
- **PiHole** loses its gravity database, and whether it recovers on its own
  depends entirely on the order of operations. Its entrypoint rebuilds gravity
  when the file is missing — but that rebuild needs working DNS, and its own
  check is `getent hosts pi.hole`, the exact call Trap 3 breaks.

  **Run `set-node-identity.sh` and restart k3s before replacing the PVC** and
  PiHole self-heals: Eastbank came back with a populated `gravity.db` and
  blocking live, no intervention. Replace the PVC while the poisoned search
  domain is still in effect and the rebuild fails silently, leaving no
  `gravity.db` at all — that was Akron, and it reads as an independent PiHole
  fault rather than as Trap 3 wearing a different face.

  If it does end up missing, `pihole -g` cannot fix it either (same failing
  check), so fix DNS first and then:
  ```bash
  kubectl -n dns exec -it deploy/pihole -- pihole -g
  ```

  Either way, run the sync Job afterwards — the entrypoint only builds from the
  default `adlists.list`, while the Job restores the adlists, groups and clients
  declared in `pihole-config.yaml` and `pihole-clients.yaml`. It does **not**
  re-run on its own: `dns-config` sets `force: true`, but that only recreates
  the Job when its spec changes, and nothing in git changed.
  ```bash
  kubectl -n dns delete job pihole-sync
  flux reconcile kustomization dns-config --with-source   # blocks, ~10m
  dig +short doubleclick.net @<site-pihole-ip>            # expect 0.0.0.0
  ```

- **Grafana dashboards** are provisioned from ConfigMaps in git and are
  unaffected.
- **NetworkOptimizer** (Eastbank's `apps` layer) loses its UniFi credentials.
  They are entered through its web UI and stored in SQLite on its PVC, not
  injected from a Secret, so they must be re-entered afterwards. Its admin
  password does come from the SOPS secret. This is the only state in this
  procedure that is not reproducible from git.

Nothing lost here is irreproducible: gravity is derived from `pihole-config.yaml`
and `pihole-clients.yaml`, dashboards are ConfigMaps, and the only genuinely
unrecoverable data is Prometheus and Loki history — which #228 accepts.

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

## Trap 3 — the FQDN hostname breaks DNS for every pod

**This cost four hours on Akron.** It is handled by
`scripts/set-node-identity.sh`, but know the shape, because the symptoms point
everywhere except DNS.

An FQDN hostname makes NetworkManager derive a search domain from it, and
kubelet copies the node's search list into every pod:

```
search dns.svc.cluster.local svc.cluster.local cluster.local akron.internal.zerpzorp.com
options ndots:5
```

`ndots:5` means any name with fewer than five dots tries **every search suffix
before the name itself**. So a pod resolving `pypi.org` first asks for
`pypi.org.akron.internal.zerpzorp.com`.

That is normally harmless — a suffix that does not exist returns NXDOMAIN and
the resolver moves to the next one. `internal.zerpzorp.com` is a real
Cloudflare-hosted zone, and Cloudflare answers **NODATA** (`NOERROR`, zero
records, SOA in authority) rather than NXDOMAIN. glibc reads NODATA as "the
name exists and has no address" and **stops**, never trying the real name.

So it only breaks under a *publicly-hosted* hostname domain. Under `home.arpa`
the same suffix would NXDOMAIN harmlessly. Moving DNS to Cloudflare in
iteration 0 and renaming the node in iteration 1 were each safe alone.

**What it looks like:** `dig` works, so DNS "looks fine". Anything using the C
library — `getent`, `ping`, `curl`, Python — fails. Observed as `pihole -g`
reporting "DNS resolution is currently unavailable" (its check is
`getent hosts pi.hole`) and the `pihole-sync` Job failing to reach pypi.org.
The host is unaffected: it defaults to `ndots:1`, so it tries real names first.

**The fix is at kubelet, not the host.** The host should *keep* its search
domain — it is the same suffix #234 adds via DHCP so short names work, and it
becomes useful once `internal.zerpzorp.com` resolves. Pods never type short
external names and gain nothing from it. `set-node-identity.sh` writes
`/etc/rancher/k3s/resolv.conf` (the host's, minus the `search` line) and adds
`kubelet-arg: "resolv-conf=..."` to `/etc/rancher/k3s/config.yaml`.

Neither takes effect until k3s restarts, and **pods keep the resolv.conf they
were created with**, so existing pods need recreating. The restart in step 4
covers both if the script runs first.

Verify after the rename:

```bash
kubectl -n dns exec deploy/pihole -- cat /etc/resolv.conf   # no <site> suffix
kubectl -n dns exec deploy/pihole -- getent hosts pi.hole   # must answer
```

`getent`, not `dig` — `dig` does not reproduce the failure.

## Trap 4 — DNS during the restart

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

2. **Confirm the DNS fallback is in place** (Trap 4), and check for stale
   host-level DNS config while you are there — the rename changes which of
   these matters:
   ```bash
   cat /etc/resolv.conf              # PiHole VIP, then 1.1.1.1, 1.0.0.1
   cat /etc/rancher/k3s/resolv.conf  # usually absent; the script creates it
   grep -n domain_name_servers /etc/dhcpcd.conf 2>/dev/null
   systemctl is-active dhcpcd
   ```
   Akron carried a stale `static domain_name_servers=127.0.0.1` in
   `dhcpcd.conf` from an earlier attempt at the #229 fallback. dhcpcd was not
   running so it was inert, but two things competing to write `/etc/resolv.conf`
   is how that bug happened in the first place. Delete it rather than leave it.

3. **Set the new identity:**
   ```bash
   sudo ./scripts/set-node-identity.sh k3s01.<site>.internal.zerpzorp.com
   ```

4. **Restart K3s** to re-register under the new name:
   ```bash
   sudo systemctl restart k3s
   kubectl get nodes -o wide
   ```

   **Registration lags the restart by 30–60 seconds.** Checked immediately, this
   shows only the *old* node, still `Ready` — that is not a failure, the new one
   has not registered yet. Wait and re-run until both appear:

   ```
   NAME                                STATUS     ROLES           AGE
   akron                               NotReady   control-plane   212d
   k3s01.akron.internal.zerpzorp.com   Ready      control-plane   66s
   ```

   The old entry going `NotReady` is the signal it has been superseded; nothing
   is running on it.

   No cordon or drain. On a single-node cluster draining only makes every pod
   `Pending` with nowhere to go, and the restart stops them anyway — the state
   they would be protecting is the state Trap 1 deletes.

5. **Delete the old Node object:**
   ```bash
   kubectl delete node <old-name>
   ```

6. **Replace the orphaned storage — delete PVCs only, never PVs.** Every PV has
   `reclaimPolicy: Delete`, so removing a PVC removes its PV. Deleting PVs by
   hand is not just redundant, it actively breaks things (see Trap 1).

   Stop the workloads first, or the delete hangs on the
   `kubernetes.io/pvc-protection` finalizer while pods still mount the volumes.
   Suspend Flux too, or it recreates the pods underneath you:
   ```bash
   flux suspend kustomization monitoring

   kubectl -n monitoring scale statefulset --all --replicas=0
   kubectl -n monitoring scale deployment  --all --replicas=0
   kubectl -n dns        scale deployment  --all --replicas=0

   kubectl -n monitoring delete pvc --all
   kubectl -n dns        delete pvc --all

   kubectl get pv,pvc -A     # both empty, or only volumes created since the rename
   ```

   Then restore. A Kustomization reconcile does not necessarily revert a manual
   `kubectl scale`, so force the charts to re-apply:
   ```bash
   flux resume kustomization monitoring
   flux reconcile kustomization monitoring --with-source

   kubectl -n monitoring get statefulset,deployment    # if still 0/0:
   flux get helmreleases -n monitoring
   flux reconcile helmrelease <name> -n monitoring --force
   ```

   Prometheus' and Alertmanager's StatefulSets are created by
   prometheus-operator rather than by Helm, so they return on their own once
   `kube-prometheus-stack-operator` is running again.

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
| Akron | 2026-08-22 |
| Eastbank | 2026-08-22 |
