# Recovering a Quiet Site

For when a site's metrics or logs have stopped reaching Akron, and for a
collector that reconnects but doesn't catch up. Changing the path on purpose
is [Changing a Site's Forwarding Path](forwarding-path-changes.md).

When forwarding fails, the remote collector's own health metrics are stuck
behind the same broken path. Akron can only see that a site has gone quiet,
not why. The checks that find the cause run at the site. Nothing alerts on a
quiet site yet (#221), so this usually starts from noticing a gap in Grafana.

**Most outages need nothing.** The collector buffers up to ADR 0003's outage
window (24 hours) and replays on reconnect, with the original timestamps. The
work here is finding the cause, spotting the rare drain that doesn't finish,
and confirming afterwards that the hole filled.

## Pre-checks

1. **Is the site actually quiet?** These list sites that sent metrics in the
   last hour but none in the last 10 minutes, and log volume per site:

   ```promql
   group by (site) (max_over_time(up[1h])) unless group by (site) (max_over_time(up[10m]))
   ```

   ```logql
   sum by (site) (count_over_time({job="pod-logs"}[10m]))
   ```

   Don't judge it by `count(up{site="<site>"})` falling to zero. A series stays
   visible for 5 minutes after its last sample, so that count trails the real
   cutoff by up to 5 minutes.

2. **These queries only work live.** Once the site recovers, its backlog fills
   the hole with the original timestamps, so the same queries run over the
   outage afterwards show nothing. To time an outage after the fact, use send
   lag (post-check 2).

## Steps

### 1. Diagnose: work from the WAN inwards

1. **The WAN.** UniFi Site Manager shows whether the site's console is online.
   Its time is *last seen*, an upper bound on last contact, not when the outage
   began. If the site is offline, there's nothing to do in the cluster. Wait for
   the WAN, then go to the post-checks.
2. **Is anything reaching Akron's Traefik?** Traefik's access log records every
   ingest request. When healthy, a remote site posts to each path every few
   seconds and gets `204`. In Grafana, against Loki:

   ```logql
   sum by (client, path, status) (count_over_time({site="akron", namespace="traefik"} |~ "POST /(api/v1/write|loki/api/v1/push)" | regexp `^(?P<client>\S+) .*POST (?P<path>\S+) HTTP[^"]*" (?P<status>\d{3})` [10m]))
   ```

   `client` is the address Traefik sees, which may not be unique per site once
   there is more than one remote site. Akron's own collector doesn't appear,
   because it writes to in-cluster Services.

   - **No requests from the site:** nothing is leaving it. It's the WAN or VPN,
     or the remote collector itself (step 4).
   - **`404`:** Akron no longer routes the path. A change merged at Akron
     before the remote site was promoted ([Changing a Site's Forwarding
     Path](forwarding-path-changes.md)).
   - **`5xx` or slow responses:** Akron's ingest is struggling (step 3).
3. **Akron's ingest.** On Akron's node:

   ```sh
   flux get hr -n monitoring
   kubectl -n monitoring get pods
   kubectl -n monitoring get endpointslices -l 'kubernetes.io/service-name in (metrics-ingest,logs-ingest)'
   ```

   Each EndpointSlice must list a pod address. A Prometheus or Loki that is
   down or restarting stops every site at once, Akron included.
4. **The remote collector.** On the quiet site's node:

   ```sh
   kubectl -n monitoring get pod collector-0
   kubectl -n monitoring top pod collector-0
   kubectl -n monitoring logs collector-0 -c alloy --since=30m | grep -iE 'level=(error|warn)' | tail -20
   ```

   Look for DNS or TLS errors against Akron's hostnames
   (`sites/<site>/monitoring/collector-hostaliases.yaml` pins them), and
   readiness failures. Send errors are expected while Akron is unreachable.

### 2. Once it reconnects: does the drain finish?

A short outage's backlog drains quickly and well inside the collector's limits
(see the appendix). How long a multi-hour backlog takes hasn't been measured.
The one known failure is a drain spiral (#270). Batches time out against a busy receiver and are
retried, and garbage collection under `GOMEMLIMIT` plus the retries use up the
CPU limit, so each batch gets slower. The signs, together:

- send lag (post-check 2) stops falling, or rises again;
- the collector's CPU sits at its limit, and its heap at `GOMEMLIMIT`
  (`base/collector/collector.yaml`):

  ```promql
  sum by (site) (rate(container_cpu_usage_seconds_total{namespace="monitoring", pod="collector-0", container="alloy"}[5m]))
  max by (site) (go_memstats_heap_inuse_bytes{job="alloy"})
  ```

  For a remote site these come through the same slow path, so step 1.4's
  `kubectl top` on its node is more current;
- its logs repeat `Failed to send batch, retrying`, and its readiness probe
  fails with `context deadline exceeded`.

Also check the receiver: Prometheus's own CPU and throttling at Akron, and
Traefik's response times in step 1.2's query. The only occurrence so far
pointed at a busy receiver, not at the WAL.

### 3. Mitigate, in this order

1. **Wait** while send lag is falling, however slowly.
2. **Relieve the receiver**, if Prometheus or Loki is at its limits.
3. **Restart the collector:** `kubectl -n monitoring delete pod collector-0`.
   The metrics WAL is replayed. The log WAL can skip unsent entries on a
   restart (grafana/alloy#7112, #517), so expect some log loss.
4. **Last resort: discard the buffer.** Everything the site hasn't delivered is
   lost, logs included.

   ```sh
   kubectl -n monitoring delete pvc wal-collector-0 --wait=false
   kubectl -n monitoring delete pod collector-0
   ```

   The StatefulSet recreates the pod on a new, empty PVC.

## Post-checks

1. **The site is sending.** Pre-check 1's query no longer lists it.
2. **Send lag is back to seconds.** During an outage it rises by 60 s every
   minute, so its history after recovery also shows when the outage began and
   ended. That makes it the clock to use, not Site Manager:

   ```promql
   max by (site) (prometheus_remote_storage_highest_timestamp_in_seconds{job="alloy"})
     - max by (site) (prometheus_remote_storage_queue_highest_sent_timestamp_seconds{job="alloy"})
   ```

3. **The hole filled.** This is the check that matters. A site sending again
   says nothing about whether its backlog arrived (#306). Over the outage
   window, compare per-target sample counts with the same length of time just
   before it. They should be equal:

   ```promql
   count_over_time(up{site="<site>"}[<window>])
   ```

   For logs, compare `sum(count_over_time({site="<site>"}[<window>]))` the same
   way. Log volume varies, so a dip isn't proof of loss, but an empty window
   is.
4. **Record the occurrence in an issue:** cause, timings, drain time, and
   whether the hole filled. Recovery that didn't fill the hole needs an issue
   of its own.

## Appendix: examples

**A WAN outage that recovered by itself (Eastbank, 2026-10-26).** Site Manager
showed the console last seen at 06:01 UTC. Send lag rose from ~8 s at 06:02 to
848 s at 06:16, then fell to 0 by 06:18. At its peak, the collector's CPU went
from ~40m to ~135m against its 500m limit, and memory went from ~450 MB to
596 MB. Post-check 3 returned 20 samples per target over 20 minutes, the same
as the baseline. Full readings are on #522.
