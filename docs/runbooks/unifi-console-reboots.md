# Rebooting or Upgrading a UniFi Console

For any reboot of a site's UDR: a UniFi OS or firmware upgrade, a deliberate
restart, or an automatic update. It covers what the reboot does to that site's
K3s node, and how to confirm the site recovered.

## What a console reboot does to its site

**The node loses its address for the length of the reboot, usually 2 to 4
minutes.** It takes `NODE_IP` from the UDR by DHCP reservation
([State That Is Not In Git](../current-state/host-state.md#off-cluster)). When
the UDR goes down, `eth0` loses carrier and NetworkManager drops the lease. It
then retries DHCP in a 45-second loop until the UDR answers again. In the
journal:

```
device (eth0): state change: activated -> unavailable (reason 'carrier-changed')
dhcp4 (eth0): state changed no lease
```

**Pods lose their route to the API server**, and log
`dial tcp 10.43.0.1:443: connect: network is unreachable`. Every controller that
holds an API lease exits with `Error` and restarts, roughly:

| Workload | Restarts |
| --- | --- |
| Flux's four controllers | ~4 each |
| cert-manager cainjector, controller | ~4, ~1 |
| kube-state-metrics | ~5 |
| node-exporter (host network, so its probe fails) | ~4 |
| system-upgrade-controller | ~1 |
| MetalLB controller | sometimes 1 |

Prometheus, Loki, Grafana, Alertmanager, the collectors, Unpoller and the MetalLB
speakers keep running. The site's scrapes have a gap of 3 to 5 minutes.

**It is expected.** No restart count here is a fault, as long as the
post-checks pass. The node keeps no static address, so nothing holds its
address through the reboot (#570).

At a remote site the reboot is also a WAN outage. The collector buffers and
replays its backlog when the link returns ([Recovering a Quiet
Site](forwarding-path-recovery.md) if it doesn't). UDP syslog sent to the site
during the reboot is lost, including Lottage's lines relayed to Eastbank.

<!-- ASSUMES(single-node): one node per console, so every API client at the site
loses the API at once. With a second node, only pods on nodes that lost their
address would restart. -->

## Pre-checks

1. **Nothing else is in flight at the site:** no change period open and no
   promotion pending. Otherwise its restarts get blamed on the change, or the
   change's failures on the reboot.
2. **Flux is healthy before you start**, so anything red afterwards is new:

   ```bash
   flux get ks -A
   flux get sources git -A
   ```

## Steps

1. Reboot or upgrade the console in UniFi.
2. Wait for the node to get its address back. On the node:

   ```bash
   journalctl -u NetworkManager -f | grep -E 'dhcp4|state change'
   ```

   A new lease with `address=<NODE_IP>` ends the outage. Allow another minute
   or two for the controllers to re-acquire their leases.

## Post-checks

1. **Every Kustomization is `Ready`:**

   ```bash
   flux get ks -A
   ```

   A layer whose dry-run hit a restarting webhook (MetalLB's, most often) can
   stay failed. Reconcile it once:

   ```bash
   flux reconcile ks <layer> --with-source
   ```

2. **Nothing is still restarting.** No pod should be in `CrashLoopBackOff`, and
   restart counts should have risen only for the workloads in the table above:

   ```bash
   kubectl get pods -A | grep -v -E 'Running|Completed'
   ```

3. **Scrapes resumed:** in Grafana, `up{site="<site>"}` is back to 1 for every
   target.

## Automatic updates

Automatic console firmware updates reboot the UDR on their own schedule, with
the same effect, and nobody is watching. Run the post-checks the next time you
look at the site. Whether automatic updates are enabled on each console isn't
recorded.

## Appendix: examples

From #570:

- **2026-10-29, both sites:** UniFi OS upgrades on all three consoles. The
  link dropped at 06:50:17 UTC, and scrapes were lost from about 06:51 to
  06:54–55. Akron's `infrastructure-config` needed a `flux reconcile` after its
  dry-run hit MetalLB's webhook.
- **2026-11-04, Akron:** a network controller upgrade. Pods logged `network is
  unreachable` from 17:57:10, and Flux re-acquired its leases at
  17:59:37–44. No reconcile was needed.
- **2026-11-05, Eastbank:** UniFi OS 5.1.35. The node was without its
  address for 2 m 16 s.
- **2026-10-15 and 2026-10-23, Akron:** UDR firmware 6.0.12 and 6.0.15,
  starting about 20:50 local, with no change recorded beforehand. The console's
  own log shows the download, `fwupdate` and reboot just before the link drop.
