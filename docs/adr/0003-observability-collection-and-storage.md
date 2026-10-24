# 3. How observability data is collected and stored

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

## Context

Every site needs metrics and logs, and an operator needs to see all sites in
one place. The constraints:

- **Storage.** Akron boots from an NVMe drive over USB3. Every other site boots
  from an SD card, where a TSDB's write amplification is the classic way to
  kill the card.
- **Headroom.** Akron runs every layer and stores everything; its CPU and memory
  are the scarce resource. Prometheus on a Pi is I/O-heavy even on SSD: when it
  was first deployed memory-starved (1 Gi, 209K series) it compacted
  continuously at ~250 MB/s and throttled the CPU at 81 °C (PRs #50–#52).
- **Network.** Sites are joined by a site-to-site VPN that can go down. PiHole is
  both each site's resolver and one of the things being monitored, so telemetry
  must not depend on it.
- Images must run on ARM64.

## Decision

**Stack:** Prometheus, Alertmanager and Grafana (kube-prometheus-stack), Loki,
and Grafana Alloy as the only collector.

**Collect at every site, store only at Akron.**

- *Collection is identical everywhere:* hardware and node metrics, Kubernetes
  metrics (kubelet, cAdvisor, kube-apiserver, kube-state-metrics, the in-cluster
  exporters) and pod logs, all shipped to Akron. Akron collects the same way; it
  is not a remote-site special case.
- *Storage is Akron-only:* Prometheus, Loki, Grafana and Alertmanager.
- *The chart does no scraping of its own.* kube-prometheus-stack's kubelet,
  apiserver, node-exporter, kube-state-metrics, controller-manager and scheduler
  monitors are disabled, so nothing is collected twice.
- *All cardinality trimming lives in the collector config*, in one place.

**The collector is the only thing at a site that crosses the VPN.** Applications
push to their local `alloy-metrics` (Loki push API), which buffers to a WAL
and forwards. Endpoints are path-scoped Traefik routes on
`websecure` (`/api/v1/write`, `/loki/api/v1/push`), with the hostnames pinned in
the collector's `/etc/hosts` at remote sites, so no resolver is in the path.

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

The collector's WAL settings and Prometheus's `outOfOrderTimeWindow` implement
the window, and each carries a comment naming this ADR.

**For logs, the window is best effort**, like much else in this design.
Alloy's only disk buffer for log entries, `loki.write`'s WAL, is experimental
and can lose unsent entries when the collector restarts mid-outage
(grafana/alloy#7112). Beyond its components, the design runs on Raspberry Pis
with SD cards, a single point of failure in power and residential internet. It
works towards the right concepts, and trades durability in the implementation
for the reality of a homelab.

**Unpoller runs once, at Eastbank**, polling every site's UniFi controller over
the VPN and writing back through its local collector (#120). Polling does not need
to sit next to the storage, and Akron's headroom is what is scarce.

**Loki runs as a single binary (the chart's `Monolithic` mode) with its
memcached caches disabled.** The scale-out path, if log volume ever justifies
it, is SimpleScalable.

**Dashboards are ConfigMaps** loaded by the Grafana sidecar, generated from JSON
in git.

**Every signal is collected by default; what is not needed is dropped.**
Metrics and logs from every workload at every site, ours or anyone else's, are
collected unless a drop excludes them:

- *Operating needs signals before the problem.* Which signal a problem needs is
  rarely known until it happens, so the default is all of them.
- *Most of what runs here is other people's software.* Its signals are defined
  upstream and change with each release. Tracking what every component emits in
  order to list it would be continual work, and would fall behind.

One rule covers everything, our own software included: a single approach is
easier to reason about than two.

**A drop is a concession to the hardware**, never a judgement that a signal is
not wanted, so it has to be revisitable when the hardware or the cost changes.
Each drop carries a `DROP` tag (CONTRIBUTING.md) recording what it saves and
when that was measured. Deciding what to drop follows guidelines, a list
expected to grow as experience does:

- Don't keep what a query can derive, including a second source of the same
  signal — unless it is used routinely (a dashboard panel, a frequent query),
  where collecting or a recording rule is cheaper than recomputing. The cost that
  matters is collector and Prometheus CPU and memory, not storage.
- No fiddly variants: extra breakdowns, labels or buckets that add cost without
  answering a new question.
- Intervals as short as the resources allow. Lengthening one is a concession to
  the hardware, and the `rate()` windows that read it must still get enough
  samples.
- A dropped signal can be restored temporarily for an investigation, carrying a
  `TEMPORARY` tag (CONTRIBUTING.md) and an issue to keep it or drop it again.

**Every change to a workload plans its observability**, whether it adds the
workload or changes it, upgrades included. Before it ships: research what the
component emits and how it is best collected, including what would be expensive
here and what to drop. After it ships: confirm the signals arrive, and settle
what is kept and dropped. Research is what keeps a new workload's first intake
from overwhelming Prometheus or Loki; tuning after deployment is the last 20%.
The procedure is [Planning a Workload's Observability](../runbooks/workload-observability.md).

**Some signals need transforming** to be useful, such as node-exporter's
`instance` set to the node name. Transformations follow collection where labels
matter, and no target is collected twice.

## Consequences

**Positive**

- No TSDB on an SD card; remote sites stay light.
- One place to query every site, and one place to trim cardinality.
- An outage within the window delays remote data instead of losing it.
- A new component is collected because it exists, so "is X monitored?" is
  answered by the cluster rather than by reading the collector's configuration.

**Negative and risks**

- **Akron is a single point of storage.** Losing its disk loses every site's
  history.
- **Two settings must stay in sync** or a remote site silently loses data across
  an outage: the collector's WAL `max_keepalive_time` and Prometheus's
  `outOfOrderTimeWindow`. Each carries a comment naming the other.
- **An outage longer than the window loses remote data**, and the loss is
  silent unless alerting reports it.
- Remote telemetry depends on Akron's Traefik.
- `loki.write`'s WAL is experimental upstream, unlike `prometheus.remote_write`'s.
  The collector has to run at the experimental stability level to use it, which
  admits every other experimental component too, and the WAL can still skip
  unsent entries after a hard kill, an OOM kill included (grafana/alloy#7112).
  That is why the log window is best effort.
- **More work per change.** Each change to a workload researches its
  observability ahead of time, re-checks production after it ships, and decides
  what is kept and dropped.
- **A new workload's first intake could overwhelm Prometheus or Loki** on this
  hardware, and become an incident. The research before it ships is the
  mitigation.
- **A default only collects what is exposed.** A component with no metrics
  endpoint, such as PiHole with no exporter, stays
  dark; how to handle each is decided when its collection is implemented.

## Alternatives considered

- **A TSDB at every site.** Rejected for SD-card wear and remote-site headroom.
- **An allowlist of what to collect.** Needs knowing in advance which signals
  will be needed, and keeping up with what other people's software emits; both
  fail silently.
- **Our own software opting in.** We know what it emits, but two approaches are
  harder to reason about than one rule, and that knowledge does not need a
  different default.
- **Components declaring their own metrics** (pod annotations, or #211). The
  same problem as an allowlist: most components are other people's software,
  whose charts do not carry the declaration, so each would need an override.
- **Promtail for logs.** End-of-life, superseded by Alloy, which also collects
  metrics, so one collector covers both.
- **Apps pushing straight to Akron.** Works, but couples every app to a remote
  endpoint and loses the WAL, so a WAN outage becomes a hole in the data.
- **Telemetry endpoints by IP**, to avoid DNS. Breaks TLS: Traefik picks the
  certificate from SNI, which an IP does not carry. `/etc/hosts` pinning gets
  the same independence from DNS.
- **Unpoller at Akron, one instance per controller.** The original layout,
  replaced to save Akron's headroom (#120).
- **Grafana Operator CRs for dashboards** (including the Unpoller chart's own
  dashboards). Adds an operator dependency, and the Unpoller chart's set omitted
  the Client DPI dashboard.
- **Loki's memcached caches.** Overhead without benefit for a single binary.
- **Scraping K3s's controller-manager and scheduler.** Embedded in the server
  binary on K3s; ~51K samples per scrape, of which ~724 series were useful (#51).
- **Mimir/Thanos for HA metrics, Tempo for traces.** Rejected for their
  footprint on Pi hardware.
- **A durable broker between collectors and Loki** (Kafka, or Loki's own Kafka
  ingestion, which is experimental). The scale-out answer to losing logs in
  transit, rejected because running a replicated broker costs more on this
  hardware than the bounded loss it prevents.
- **A second collector for logs only** (Vector or Fluent Bit, for their mature
  disk buffers). Breaks "Alloy as the only collector" for a guarantee not yet
  shown to be missing in practice.
