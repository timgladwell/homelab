# 3. How observability data is collected and stored

- **Status:** Proposed
- **Date:** 2026-10-06
- **Deciders:** Tim Gladwell

## Changelog

| Date | By | Description |
| --- | --- | --- |
| 2026-10-06 | Tim and Claude | Proposed, from CLAUDE.md's observability section and the decisions in `monitoring-tasks.md`, each checked against the code (#413) |

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
- *All cardinality trimming lives in the collector config*
  (`alloy-metrics.alloy`), in one place.

**The collector is the only thing at a site that crosses the VPN.** Applications
push to their local `alloy-metrics` (Loki push API on `alloy-metrics:3100`),
which buffers to a WAL and forwards. Endpoints are path-scoped Traefik routes on
`websecure` (`/api/v1/write`, `/loki/api/v1/push`), with the hostnames pinned in
the collector's `/etc/hosts` at remote sites, so no resolver is in the path.

**Unpoller runs once, at Eastbank**, polling every site's UniFi controller over
the VPN and writing back through its local collector (#120). Polling does not need
to sit next to the storage, and Akron's headroom is what is scarce.

**Loki runs in SingleBinary mode with its memcached caches disabled.** The
scale-out path, if log volume ever justifies it, is SimpleScalable.

**Dashboards are ConfigMaps** loaded by the Grafana sidecar, generated from JSON
in git.

**What gets collected:**

- Don't collect what a query can derive, including a second source of the same
  signal — unless it is used routinely (a dashboard panel, a frequent query),
  where collecting or a recording rule is cheaper than recomputing. The cost that
  matters is collector and Prometheus CPU and memory, not storage.
- No fiddly variants: extra breakdowns, labels or buckets that add cost without
  answering a new question.
- Intervals as short as the resources allow. Lengthening one is a concession to
  the hardware, and the `rate()` windows that read it must still get enough
  samples.
- A metric is added either as a **base metric** — answering a question that will
  come up again, with its keep/drop rule saying which — or **temporarily** during
  an investigation, carrying a `TEMPORARY` tag (CONTRIBUTING.md) and an issue to
  promote or remove it.

## Consequences

**Positive**

- No TSDB on an SD card; remote sites stay light.
- One place to query every site, and one place to trim cardinality.
- A WAN outage delays remote data instead of losing it.

**Negative and risks**

- **Akron is a single point of storage.** Losing its disk loses every site's
  history.
- **Two settings must stay in sync** or a remote site silently loses data across
  an outage: the collector's WAL `max_keepalive_time` and Prometheus's
  `outOfOrderTimeWindow`. Each carries a comment naming the other.
- Remote telemetry depends on Akron's Traefik.
- `loki.write`'s WAL is experimental upstream, unlike `prometheus.remote_write`'s.

## Alternatives considered

- **A TSDB at every site.** Rejected for SD-card wear and remote-site headroom.
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
- **Loki's memcached caches.** Overhead without benefit in SingleBinary mode.
- **Scraping K3s's controller-manager and scheduler.** Embedded in the server
  binary on K3s; ~51K samples per scrape, of which ~724 series were useful (#51).
- **Mimir/Thanos for HA metrics, Tempo for traces.** Rejected for their
  footprint on Pi hardware.
