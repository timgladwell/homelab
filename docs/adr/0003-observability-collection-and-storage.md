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
| 2026-10-25 | Tim and Claude | The collector is defined by its contract, not its processes, as the interface of one observability domain; LAN devices reach it through Traefik by default, the cluster through an internal service; buffering starts at a collector; mobile senders; a sending and a forwarding site, and names standardised alongside drops (#524) |

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
  must not depend on it. Some links are metered and slow, such as Lottage's.
- **Senders.** Most telemetry comes from equipment that stays at one site: its
  cluster's workloads and nodes, and devices on its LAN such as the UDR,
  printers, desktop computers and landline phones, some of which speak only UDP
  syslog. Some comes from mobile equipment that roams between sites and off the
  network, such as laptops, tablets and mobile phones.
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

**Each site has one logical collector, defined by its contract.** Everything a
site observes is pulled into it or pushed at it, and it forwards everything to
Akron. Its contract is what senders and storage depend on: the push APIs and
receivers it serves, the labels it guarantees on every signal, and where it
forwards. That may be several APIs and several scrapers. How many processes
run behind it, and of what kind, is not part of the contract. That changes
without either side changing, and a change to the contract changes both sides
together.

**Observability is one domain, and its interface is each site's ingest
points.** The collectors, storage, Grafana and Alertmanager at every site form
a single observability domain. Each site publishes ingest points: to its local
network, such as syslog and OTLP over HTTP, and to its own cluster, an internal
collector service. Senders depend on those and on nothing else. Data moving east-west between sites stays inside the domain: a
remote site's collector writes straight to Akron's storage over the VPN, and
does not go back out through Akron's ingest points, any more than a call
within a module goes back out through the module's interface. Nothing outside
the domain depends on how it works inside; within it, that restriction does
not apply.

**The collector is the only thing at a site that crosses the VPN.** It buffers
to a WAL and forwards. Akron's storage ingest endpoints are path-scoped Traefik
routes on `websecure` (`/api/v1/write`, `/loki/api/v1/push`), with the
hostnames pinned in the collector's `/etc/hosts` at remote sites, so no
resolver is in the path.

**Devices on a site's LAN reach the collector through Traefik by default**,
wherever their protocol allows it: OTLP and the Loki push API over HTTPS. The
proxy keeps each sender apart from whatever runs behind the contract, and gives
it the site's TLS certificate. **The cluster's own workloads and nodes send to
an internal collector service** instead, which is their only observability
dependency; routing them through Traefik would add overhead and buy nothing.
Ideally every emitter would be kept apart from every collector by a proxy or a
service, and an abstract protocol. Software we write fits that: it pushes OTLP,
and writes structured logs to stdout and stderr. Other people's software often
doesn't, because it expects a backend's own URL, such as a Prometheus
remote-write or Loki push endpoint, or expects to be scraped. So this is a
default, not an enforced rule. An exception is made where keeping it costs more
than it buys: where building and hosting a proxy for a backend-specific API is
not worth it, or for a real saving in CPU or memory. Each exception is recorded
where it is made.

**Every hop that leaves a node is encrypted, where the sender supports it.**
Plaintext is accepted only from senders that offer nothing else, which in
practice means UDP syslog from devices like the UDR, printers and landline
phones. Anyone on the VLAN can forge such lines, and a compromised sender
controls whether it sends at all. Traffic between pods on one node is not
covered. Authentication is added where a sender supports it, and is not
required. The concern this rule answers is data on the wire in clear, not a
rogue sender.

**Ingest is internal only.** No observability endpoint is reachable from the
internet. Every sender is on a site's LAN or the site-to-site VPN.

**Equipment that stays at one site sends to that site's collector.** A site
with no collector of its own sends to another site's collector.

**Buffering starts at a collector.** Sites that run this repo's cluster
(Akron, Eastbank) collect, buffer and forward. The outage window applies from
their collectors onward, where loss is not part of the design, only a
consequence of the trade-offs above. Everything else sends straight to a
collector, unbuffered, and what it loses before arriving there is accepted.
That covers a device at a site, a site whose server this repo doesn't manage
(Lottage), and mobile equipment.

**Mobile senders send to one fixed collector**, Akron's, because that is where
mobile equipment spends most of its time. Off the network, its telemetry is
dropped. **A mobile sender stops itself on a metered or slow link.** The
collector cannot stop a sender from using a link, so that duty is the
sender's.

**Every signal carries two sites: the one that sent it and the one that
forwarded it.** For a cluster's own signals they are the same. They differ for
a device relayed through another site's collector and for a mobile sender, and
both are diagnostic: where the data came from, and which path it took.

**Names and key values are standardised in the collector, alongside drops.**
A sender's names reach storage only after the collector has translated them to
one convention. That covers metric names arriving over OTLP, and label values
such as site names that a sender spells its own way (Unpoller's `Akron`, for
example). Doing it in the same place as drops keeps one place to read for what
storage receives. The conventions are common, published ones rather than our
own wherever one exists. They are in [Appendix: naming
conventions](#appendix-naming-conventions).

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
- The collector's implementation can be reshaped (merged, split, replaced)
  without touching any sender or storage, as long as the contract holds.
- A relayed or mobile sender's data says both where it came from and which
  path it took.

**Negative and risks**

- **Akron is a single point of storage.** Losing its disk loses every site's
  history.
- **Two settings must stay in sync** or a remote site silently loses data across
  an outage: the collector's WAL `max_keepalive_time` and Prometheus's
  `outOfOrderTimeWindow`. Each carries a comment naming the other.
- **An outage longer than the window loses remote data**, and the loss is
  silent unless alerting reports it.
- Remote telemetry depends on Akron's Traefik, and each site's LAN devices
  depend on their own site's Traefik.
- **Plaintext syslog can be forged** by anyone on the VLAN, and any sender can
  push to an ingest endpoint unless it authenticates.
- **Nothing is buffered before a collector.** A device's data is lost while its
  collector is unreachable. Lottage's devices reach theirs across the VPN, so
  any VPN outage loses their data.
- **A mobile sender's data depends on the sender.** It's lost off the network
  and wherever the sender has stopped itself, and its sending site is only as
  accurate as what it reports.
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
- **A mobile sender pushing straight to Akron's storage ingest.** The WAL
  argument above is weaker for a sender whose loss off the network is accepted
  anyway, but its data would skip the collector, where all trimming and
  labelling live.
- **A mobile sender using its current site's collector**, through a site-local
  name that each site's PiHole answers with its own Traefik address. It works,
  and every site's certificate already covers such names. Not chosen yet: one
  fixed collector is simpler to start with, but nothing yet shows that running
  a mobile sender's receivers at every site is costly. Observability endpoints
  with DNS names standardised across sites would make it cheap, and reopen it.
- **Defining the collector by its processes** (one process, or one release).
  It would tie senders to choices that should stay free to change: running as
  root to read host logs, and isolating push traffic from scraping.
- **Identifying a sender's site by its client IP**, matched against each site's
  subnets at the collector. It needs a subnet map kept in step with the network.
- **Translating OTLP metric names in Prometheus's own OTLP receiver**, at
  Akron. It would send OTLP metrics around the collector's remote-write path
  and its WAL, so one kind of metric would travel and be buffered differently
  from the rest.
- **Authenticating senders with mTLS.** Deferred since #228. Every endpoint is
  internal only, every site is one node, and the concern is data in clear,
  which TLS already covers. A site with a second node, or an endpoint reachable
  beyond the LAN and VPN, reopens it.
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

## Appendix: naming conventions

The conventions the collector translates to (see *Names and key values are
standardised in the collector*). The names chosen for each label, and the
lists of accepted values, are kept in the
[naming convention](../current-state/naming-convention.md).

- **Two site labels on every signal:** the sending site and the forwarding
  site.
- **Key values are lowercase**, site names included (`akron`, not `Akron`),
  because lowercase keeps queries simple. The exception is a value that has to
  be camel case.
- **Accepted values are listed for reference, not enforced.** Developers read
  the list to plan the transformations they need. A new value turning up isn't
  a failure: it gets checked against these conventions and added to the list.
- **Metric names arriving over OTLP are translated in the collector** by
  Alloy's `otelcol.exporter.prometheus` (dots to underscores, unit and `_total`
  suffixes), not by our own rules. That way, OTLP metrics take the same network
  path and the same buffer as every other metric.
