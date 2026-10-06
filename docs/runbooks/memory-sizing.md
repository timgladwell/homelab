# Sizing a Container's Memory Limit

For setting or changing any container's memory limit, and for chasing a
container that throttles or fails health checks near its limit without an
obvious leak.

**A memory limit must cover the process *and* the files it actively reads**:
its binary, assets, databases. Page cache is charged to the container's memory
limit. When a program's working files do not fit beside its process memory, it
evicts and re-reads its own files continuously; that reclaim counts against its
CPU quota, so the container throttles and misses health checks while doing no
more work than usual. The cert-manager webhook (#375: a 57.9 MB binary in a
64 Mi limit) and Grafana (#378) both failed this way, and both were first misread
as CPU or leak problems.

## Pre-checks

1. **Is the node thrashing at all?** Node-wide major faults, normally near 0/s:

   ```promql
   rate(node_vmstat_pgmajfault{site="<site>"}[10m])
   ```

   This is node-wide, so it says *that*, not *which container*. Which containers
   run close to their limit, worst case over 7 days:

   ```promql
   sort_desc(max by (site, namespace, container) (max_over_time((container_memory_cache{container!="", container!="POD"} + container_memory_rss{container!="", container!="POD"})[7d:10m]))
     / max by (site, namespace, container) (kube_pod_container_resource_limits{resource="memory"}) > 0.9)
   ```

2. **Do not size from `kubectl top` or `container_memory_working_set_bytes`.**
   Working set churns under reclaim and looks like a leak (#323's sawtooth).
   **A full page cache is normal** — the kernel fills spare room by design.

## Steps

1. **Read the container's own cgroup** on the host — not the pod's, which
   aggregates sidecars:

   ```sh
   mstat() {  # usage: mstat <namespace> <label-selector> <container>
     ns=$1; sel=$2; c=$3
     CID=$(kubectl -n "$ns" get pod -l "$sel" -o jsonpath="{.items[0].status.containerStatuses[?(@.name==\"$c\")].containerID}" | sed 's|.*://||')
     D=$(sudo find /sys/fs/cgroup -type d -name "*${CID}*" | head -1)
     echo "== $ns/$c $(date -u +%T)"
     sudo grep -E '^(anon|file|file_mapped|pgscan_direct|pgsteal_direct|workingset_refault_file) ' "$D/memory.stat"
     echo "current $(sudo cat "$D/memory.current")  max $(sudo cat "$D/memory.max")"
   }
   ```

2. **Take two reads five minutes apart, under representative load** — including
   a cold start after idling, and whatever the program's heaviest routine work is.
   - `pgscan_direct` and `workingset_refault_file` **rising between reads**:
     active thrash.
   - Flat counters with a large `file`: a normal full cache.
   - A large *lifetime* `pgscan_direct` proves the container has hit its own
     limit at some point, even if it is far below it now — a cgroup only does
     direct reclaim at its own `memory.max`.
3. **Size the limit to `anon` at peak plus the actively used `file`**
   (`file_mapped` is a floor for the latter). Record the reads and the
   arithmetic in the issue driving the change, and link that issue from a
   comment beside the limit.
4. **Raise the limit, not the request.** Page cache is reclaimable and limits
   reserve no node capacity.
5. **Recompute the namespace `ResourceQuota` in the same PR**, including the
   rolling update's second pod.

## Post-checks

After the change rolls out, repeat step 2 under the same load: both counters
flat between reads, and no liveness or readiness failures for the container.
