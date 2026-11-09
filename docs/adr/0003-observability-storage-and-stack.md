# 3. Where observability data is stored, what survives an outage, and on what stack

- **Status:** Proposed
- **Date:** 2026-10-06
- **Deciders:** Tim Gladwell

## Changelog

| Date | By | Description |
| --- | --- | --- |
| 2026-10-06 | Tim and Claude | Proposed, from CLAUDE.md's observability section and the decisions in `monitoring-tasks.md`, each checked against the code (#413) |
| 2026-10-16 | Tim and Claude | Outage window stated as a 24-hour target, with what it does not cover (#388) |
| 2026-10-16 | Tim and Claude | Everything running is collected by default, excluded only for the hardware's sake; formalised after #323 showed what an uncollected component costs (#367) |
| 2026-10-24 | Tim and Claude | The log window is best effort, after grafana/alloy#7112 showed the log WAL losing entries on a restart mid-outage; a broker and a second collector rejected for now (#517) |
| 2026-10-25 | Tim and Claude | The collector is defined by its contract, not its processes, as the interface of one observability domain; LAN devices reach it through Traefik by default, the cluster through an internal service; buffering starts at a collector; mobile senders; a sending and a forwarding site, and names standardised alongside drops (#524) |
| 2026-10-27 | Tim and Claude | Each site's ingest points, the two processes behind them, receivers opted into per site, and a lenient UDP syslog ingest on its own address. Mobile senders use the site they are at, and one site label set by the ingest point replaces the sending and forwarding pair (#512) |
| 2026-10-29 | Tim and Claude | Collecting by default is a denylist of metrics, separate from finding targets: metrics port names by default, the `prometheus.io/scrape` annotation for stragglers; an annotation is no longer counted as an allowlist (#516) |
| 2026-10-29 | Tim and Claude | The storage stack's self-monitoring stays with the chart, as the one collection path outside the collector, and carries `site` like every other signal; aggregations keep `site` (#511) |
| 2026-11-05 | Tim and Claude | The syslog address is its own Component, shared by every syslog sender. The second sender (the OBi100) showed UniFi's receiver owned it |
| 2026-11-06 | Tim and Claude | Split into one problem per ADR: what is collected moves to [ADR 0008](0008-what-observability-collects.md), the ingest contract to [ADR 0009](0009-observability-ingest-contract.md); this ADR keeps storage, durability and the stack, which aren't separable (#617) |
| 2026-11-06 | Tim and Claude | Unpoller's placement moves to [ADR 0009](0009-observability-ingest-contract.md), as the example of a rule for exporters (#624 review) |
| 2026-11-06 | Tim and Claude | Every dashboard is a copy in Akron's git, not shipped by a workload's chart; the stack's own chart is the exception (#624 review) |
| 2026-11-09 | Tim and Claude | A sender retries until its buffer ages the data out: a retry budget shorter than the WAL cut the log window to ~8½ minutes (#662) |

## Context

Every site needs metrics and logs, and an operator needs to see all sites in
one place. What is collected is
[ADR 0008](0008-what-observability-collects.md), and how it reaches storage,
whatever the stack, is [ADR 0009](0009-observability-ingest-contract.md). This
ADR decides where the data is stored, what survives an outage, and the stack
that does it. Those aren't separable: changing the stack changes how
durability works.

The constraints:

- **Storage.** Akron boots from an NVMe drive over USB3. Every other site boots
  from an SD card, where a TSDB's write amplification is the classic way to
  kill the card.
- **Headroom.** Akron runs every layer and stores everything; its CPU and memory
  are the scarce resource. Prometheus on a Pi is I/O-heavy even on SSD: when it
  was first deployed memory-starved (1 Gi, 209K series) it compacted
  continuously at ~250 MB/s and throttled the CPU at 81 °C (PRs #50–#52).
- **Network.** Sites are joined by a site-to-site VPN that can go down.
- Images must run on ARM64.

## Decision

**Stack:** Prometheus, Alertmanager and Grafana (kube-prometheus-stack), Loki,
and Grafana Alloy as the only collector.

**Collect at every site, store only at Akron.**

- *Every site runs a collector* and ships everything to Akron.
- *Storage is Akron-only:* Prometheus, Loki, Grafana and Alertmanager.
- *The chart scrapes only the storage stack itself.* kube-prometheus-stack's
  kubelet, apiserver, node-exporter, kube-state-metrics, controller-manager and
  scheduler monitors are disabled, so nothing is collected twice. Its own
  monitors for Grafana, Prometheus, Alertmanager and the operator stay: they
  are the chart's integration (ADR 0005), its default rules select on the jobs
  they produce, and they exist only where storage runs. That is the one
  collection path outside the collector. Prometheus stamps `site` on it, so
  [ADR 0009](0009-observability-ingest-contract.md)'s label guarantee still
  holds.

**Buffering starts at a collector.** Sites that run this repo's cluster
(Akron, Eastbank) collect, buffer to a WAL and forward. The outage window
applies from their collectors onward, where loss is not part of the design,
only a consequence of the trade-offs above. Everything else sends straight to a
collector, unbuffered, and what it loses before arriving there is accepted.
That covers a device at a site, a site whose server this repo doesn't manage
(Lottage), and mobile equipment.

**Remote data survives an outage of up to 24 hours.** An outage is anything
that stops a collector delivering to Akron: the WAN, the VPN, or Akron itself.
The collector holds what it cannot send for that long, and Akron's Prometheus
accepts samples that late; past it, the oldest data is dropped. The window is a
Pareto choice. A short one is cheap and covers most outages, and each extra day
costs SD-card space and wear at remote sites, a wider out-of-order window at
Akron, and a longer drain on reconnect (#270).

- *Covered:* ISP and WAN drops, upgrades and reboots, an evening's power cut.
- *Not covered, accepted as loss:* a multi-day outage at Akron while nobody is
  there to fix it (#388), and waiting for replacement hardware. Alerting is what
  makes that loss visible.

**A sender retries until its buffer ages the data out, and never gives up
sooner.** The buffer's age limit is the window only while the sender holding it
keeps trying. A retry budget shorter than the buffer silently shortens the
window to the budget: logs were lost after about 8½ minutes of a Loki outage
while the WAL still held them (#662). This applies to every hop inside a
site's cluster, the node log reader's included, whose buffer is the journal and
pod log files it reads. Errors that no retry can fix, a malformed or rejected
entry, are still dropped at once.

The collector's WAL and retry settings and Prometheus's `outOfOrderTimeWindow`
implement the window, and each carries a comment naming this ADR.

**For logs, the window is best effort**, like much else in this design.
Alloy's only disk buffer for log entries, `loki.write`'s WAL, is experimental
and can lose unsent entries when the collector restarts mid-outage
(grafana/alloy#7112). Beyond its components, the design runs on Raspberry Pis
with SD cards, a single point of failure in power and residential internet. It
works towards the right concepts, and trades durability in the implementation
for the reality of a homelab.

### The collector behind the contract

[ADR 0009](0009-observability-ingest-contract.md) defines the collector by its
contract and leaves its processes free to change. This is how Alloy implements
it.

**Two processes**, at every site that runs this repo's cluster, Akron
included. Akron's forwarder writes to storage inside its own cluster, and every
other site's writes over the VPN.

- *The forwarder* runs without root and holds the WAL. It scrapes, serves
  every push receiver (syslog included), applies the standardising relabels
  and drops, and runs both writers to Akron.
- *The node log reader* runs as root, because it reads pod logs and the
  journal from the host, and does nothing else. It forwards what it reads to
  the forwarder over the in-cluster Loki push API, rather than writing to
  storage itself, so every signal passes one set of relabels and one buffer.
  It runs as a DaemonSet at every site. It holds no state worth a volume, and a
  DaemonSet is right on any number of nodes.

Root stays on the smallest surface it can: a file reader with no network
listener. A burst of pod logs still lands in the forwarder's CPU and memory,
but it arrives through a push API that can be rate-limited.

**Push receivers run in the forwarder.** That's one hop, and everything goes
into the WAL. The cost is that a push burst shares CPU and memory with
scraping (#270). It sits behind the contract, so moving receivers into a
process of their own later doesn't touch any sender.

### The rest of the stack

**Loki runs as a single binary (the chart's `Monolithic` mode) with its
memcached caches disabled.** The scale-out path, if log volume ever justifies
it, is SimpleScalable.

**Every dashboard is a copy of its JSON in Akron's git**, whatever its source
(grafana.com, a workload's chart, or our own), loaded into Grafana as a
ConfigMap by the sidecar. A workload's chart doesn't ship its own dashboard:

- *A chart at a remote site can't reach Akron's Grafana.* The sidecar sees
  only the cluster it runs in.
- *A copy can be edited to match what is stored.* Standardisation renames
  labels a packaged dashboard selects on
  ([ADR 0008](0008-what-observability-collects.md)), and dashboards from
  grafana.com need their datasource inputs resolved.
- *A copy is pinned.* It changes only when git does, not on a chart upgrade.

The exception is the storage stack's own chart. kube-prometheus-stack's
built-in dashboards are its integration (ADR 0005), they live where Grafana
does, and the collector reproduces the names they select on
([ADR 0008](0008-what-observability-collects.md)), so they ship with the chart.

The cost is that updates are by hand: a dashboard from grafana.com records its
source in its `gnetId`, and an update is a fresh copy with the same edits
reapplied.

## Consequences

**Positive**

- No TSDB on an SD card; remote sites stay light.
- One place to query every site.
- An outage within the window delays remote data instead of losing it.

**Negative and risks**

- **Akron is a single point of storage.** Losing its disk loses every site's
  history.
- **Two settings must stay in sync** or a remote site silently loses data across
  an outage: the collector's WAL `max_keepalive_time` and Prometheus's
  `outOfOrderTimeWindow`. Each carries a comment naming the other.
- **An outage longer than the window loses remote data**, and the loss is
  silent unless alerting reports it.
- **Nothing is buffered before a collector**, so a sender loses what it sends
  while its collector is unreachable.
- **A batch the receiver keeps refusing as temporary holds up everything behind
  it.** Retrying until the buffer ages out means a receiver that answers one
  batch with a 5xx forever stalls that sender until the window passes. It ends
  in the same loss as an outage of that length, and is as silent unless send
  lag is watched.
- **Push bursts and scraping share the forwarder's CPU and memory.**
- `loki.write`'s WAL is experimental upstream, unlike `prometheus.remote_write`'s.
  The collector has to run at the experimental stability level to use it, which
  admits every other experimental component too, and the WAL can still skip
  unsent entries after a hard kill, an OOM kill included (grafana/alloy#7112).
  That is why the log window is best effort.

## Alternatives considered

- **A TSDB at every site.** Rejected for SD-card wear and remote-site headroom.
- **Promtail for logs.** End-of-life, superseded by Alloy, which also collects
  metrics, so one collector covers both.
- **Grafana Operator CRs for dashboards** (including the Unpoller chart's own
  dashboards). Adds an operator dependency, and the Unpoller chart's set omitted
  the Client DPI dashboard.
- **Dashboards shipped by each workload's chart**, picked up by the sidecar
  from any namespace. Works only for workloads at Akron, and the dashboard
  can't be edited to match standardised names.
- **Loki's memcached caches.** Overhead without benefit for a single binary.
- **Mimir/Thanos for HA metrics, Tempo for traces.** Rejected for their
  footprint on Pi hardware.
- **A durable broker between collectors and Loki** (Kafka, or Loki's own Kafka
  ingestion, which is experimental). The scale-out answer to losing logs in
  transit, rejected because running a replicated broker costs more on this
  hardware than the bounded loss it prevents.
- **A second collector for logs only** (Vector or Fluent Bit, for their mature
  disk buffers). Breaks "Alloy as the only collector" for a guarantee not yet
  shown to be missing in practice.
- **One collector process for everything.** A simpler model, but root would
  extend to the WAL and every network listener, and a log flood would share
  the reader's CPU with the WAL drain (#270).
- **A separate process for push receivers.** It keeps push traffic apart from
  scraping, but adds a second hop that needs a buffer of its own.
- **Moving the node log reader to a StatefulSet.** Only worth it for a single
  merged process with a WAL. The reader has nothing to persist.
