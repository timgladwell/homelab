# Changing a Site's Forwarding Path

For any change to how a site's collector delivers to Akron: an ingest URL, the
`endpoint` block of `prometheus.remote_write` or `loki.write` in
`base/collector/collector.alloy`, the collector's StatefulSet or PVC name, or
the receiving end at Akron (its ingest IngressRoutes and Services). Recovering
a path that broke on its own is [Recovering a Quiet
Site](forwarding-path-recovery.md).

**Each of these changes orphans the buffer.** The collector holds what it
can't send in a write-ahead log (WAL) on its PVC, and ADR 0003's outage window
depends on that WAL being replayed. Replay only happens into the same queue:

- **Metrics:** the queue is keyed by a hash of the endpoint config, visible as
  the `remote_name` label on `prometheus_remote_storage_*`. Change the URL or
  anything else in the `endpoint` block and the collector starts a new queue
  with no stored progress. The old backlog is never offered (#306).
  Prometheus's out-of-order window doesn't help, because the samples are
  never sent.
- **Logs:** assumed to behave the same on a URL change. That hasn't been
  checked against the source, so treat it as orphaning.
- **A renamed StatefulSet or PVC** starts on an empty volume. The old PVC keeps
  the backlog and nothing reads it.

Whatever was buffered at the moment of the change is lost, unless you plan the
change so that nothing is buffered. Data loss can be acceptable, but it should
be a decision, not a surprise.

## Pre-checks

1. **Decide: drain first, or accept the gap.**
   - **Accept the gap** when the loss is small and known: a few minutes of one
     site's data. Say so in the PR, with the expected window.
   - **Drain first** when the gap matters. The old path must keep accepting
     until the queue is empty, so the change happens in two PRs. The first adds
     the new path *beside* the old one, for example a second IngressRoute at
     Akron. Then switch the collector and promote. The second PR removes the
     old path once every site has moved. A change that alters Akron's receiver
     in the same PR as the remote collector's URL can't drain. Akron applies it
     at merge, but the remote site only applies it once promoted, so the
     remote site buffers into a queue that is orphaned when it moves.
2. **Nothing is backlogged now.** Send lag per site should be seconds, not
   minutes. Don't change the path during an outage.

   ```promql
   max by (site) (prometheus_remote_storage_highest_timestamp_in_seconds{job="alloy"})
     - max by (site) (prometheus_remote_storage_queue_highest_sent_timestamp_seconds{job="alloy"})
   ```

   Don't use `prometheus_remote_storage_samples_pending`. It sits flat while
   the collector sends normally, so it doesn't track the backlog (#522).
3. **Note what to compare afterwards:** the target count and the current
   queue's identity.

   ```promql
   count by (site) (up == 1)
   count by (site, remote_name, url) (prometheus_remote_storage_queue_highest_sent_timestamp_seconds{job="alloy"})
   ```

## Steps

1. **Merge.** Akron applies it within Flux's interval. Akron's own collector
   writes to in-cluster Services, so apart from its restart gap it's affected
   only if the change touches those.
2. **Verify Akron**, on Akron's node:

   ```sh
   flux get hr -n monitoring collector
   kubectl -n monitoring get sts,pvc | grep collector
   kubectl -n monitoring logs collector-0 -c alloy --since=10m | grep -iE 'level=(error|warn)' | head
   ```

   Two `marker segment file does not exist` warnings at startup are normal.
3. **Promote to `stable`** ([Promoting to `stable`](promote-to-stable.md)), and
   verify each remote site with the same commands on its node. If the change
   renames the StatefulSet, the namespace ResourceQuota may refuse the new pod
   until Flux prunes the old one (a `FailedCreate` event). That clears by
   itself.

## Post-checks

1. **The new queue exists, and send lag is back to seconds.** Run pre-check 3's
   `remote_name` query again. A changed endpoint shows a new `remote_name`,
   and the old one goes stale.
2. **The target count matches pre-check 3.**
3. **The gap is what you expected.** Over the change window, compare
   `count_over_time(up{site="<site>"}[<window>])` with the same length of time
   before it. Record the measured gap in the PR's issue.
4. **Delete an orphaned PVC**, if the change renamed one, after checking that
   only the old one is listed:

   ```sh
   kubectl -n monitoring get pvc
   kubectl -n monitoring delete pvc <old-pvc>
   ```
