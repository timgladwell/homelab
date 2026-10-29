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
| 2026-10-27 | Tim and Claude | Each site's ingest points, the two processes behind them, receivers opted into per site, and a lenient UDP syslog ingest on its own address. Mobile senders use the site they are at, and one site label set by the ingest point replaces the sending and forwarding pair (#512) |
| 2026-10-29 | Tim and Claude | Collecting by default is a denylist of metrics, separate from finding targets: metrics port names by default, the `prometheus.io/scrape` annotation for stragglers; an annotation is no longer counted as an allowlist (#516) |
| 2026-10-29 | Tim and Claude | The storage stack's self-monitoring stays with the chart, as the one collection path outside the collector, and carries `site` like every other signal; aggregations keep `site` (#511) |

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
- *The chart scrapes only the storage stack itself.* kube-prometheus-stack's
  kubelet, apiserver, node-exporter, kube-state-metrics, controller-manager and
  scheduler monitors are disabled, so nothing is collected twice. Its own
  monitors for Grafana, Prometheus, Alertmanager and the operator stay: they
  are the chart's integration (ADR 0005), its default rules select on the jobs
  they produce, and they exist only where storage runs. That is the one
  collection path outside the collector. Prometheus stamps `site` on it, so
  the label guarantee below still holds.
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

**Mobile senders send to the collector of the site they are at**, through a
site-local name that each site's PiHole answers with its own Traefik address.
The sender needs no idea of where it is: the ingest point that receives its
data supplies the site. Off the network, its telemetry is dropped. **A mobile
sender stops itself on a metered or slow link.** The collector cannot stop a
sender from using a link, so that duty is the sender's.

**Every signal carries the site whose ingest point received it**, set by the
collector rather than trusted from the sender. For the cluster's own signals,
devices on the site's LAN and mobile senders, that is where the signal came
from. The one exception is a device at a site with no collector of its own,
whose receiving collector stamps the device's site explicitly. Which site
*forwarded* a signal isn't recorded: every collector forwards to Akron, so it
would only repeat the site.

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

**Which metrics are kept is a denylist; finding the targets is separate.**
Everything a target serves is kept, and tuning is choosing drops from measured
data, never researching what to list. Targets are found by the common metrics
port-name conventions by default, and a pod those miss declares its port with
the `prometheus.io/scrape` annotations. The name conventions stay narrow,
because a looser pattern starts catching ports that are not metrics.

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

### The site collector

**Every site publishes these ingest points.** Their names are in the [naming
convention](../current-state/naming-convention.md).

| Ingest point | Protocol | Reached through | Sent by |
| --- | --- | --- | --- |
| The site's collector hostname, and the site-local collector name: `/v1/logs`, `/v1/metrics`, `/loki/api/v1/push` | OTLP and Loki push, over HTTPS | Traefik, `websecure` | devices on the site's LAN, by either name; mobile senders, by the site-local name |
| The internal collector Service: the same paths | OTLP and Loki push, over HTTP inside the node | cluster DNS | the cluster's own workloads, such as our software and Unpoller |
| The site's syslog address, one UDP port per stream | syslog, plaintext | its own LAN address | devices: the UDR, printers, phones |
| — | Prometheus scrape | in-cluster discovery | every workload that exposes metrics |

The LAN and cluster ingest points serve the same receivers, so a sender gets
the same behaviour either way. Every signal leaves the collector carrying its
site, a host label for off-cluster senders, and a `job` naming the stream. The
collector writes metrics and logs to Akron's storage. OTLP is served at every
site, not only Akron's, because our own software pushes it wherever it runs,
and that is what lets a mobile sender use whichever site it is at.

**Behind the contract, two processes**, at every site that runs this repo's
cluster, Akron included. Akron's forwarder writes to storage inside its own
cluster, and every other site's writes over the VPN.

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

**UDP syslog gets its own LAN address at each site**, a LoadBalancer Service
selecting the forwarder, and a DNS record. This is an exception to reaching
the collector through Traefik. Traefik can carry UDP on its existing address,
but it proxies the datagram, so the collector would see Traefik as the sender
and lose the device's address. For devices that leave the syslog hostname
empty, that address is the only host label there is. With
`externalTrafficPolicy: Local` on its own Service, the address reaches the
collector. TLS syslog is added beside it if a sender turns out to support it.

**The syslog ingest is lenient: a line is never dropped for failing to
parse.** UniFi is loose with the format. One stream mixes standard syslog
lines, applications' own log formats and kernel debug dumps, and another omits
the syslog priority header. A strict RFC 3164 parser discards whatever it
can't read: Akron's dropped 145 of about 352,000 lines in a week. So the
receiver takes each line raw, and parsing the header is a best-effort step
after it. A line that doesn't parse is kept whole and labelled as unparsed,
and counted, so the formats it misses show up instead of disappearing.

**Sites opt into receivers; base never knows which.** Each reusable receiver,
such as UniFi's syslog parsing, is a Kustomize Component in `base/`. It adds
one Alloy file to the forwarder's configuration and the Service that exposes
it, and a site lists the Components it runs. The forwarder loads its
configuration as a directory, so a receiver's file joins the base file without
editing it. A receiver depends on a stable entry point that the base file
always defines, and the base file depends on no receiver, which keeps the
layering direction [ADR 0004](0004-repository-layering.md) requires. A
receiver's file isn't valid on its own, so validation checks each site's
assembled configuration, not the files one at a time.

**Until Lottage runs a collector of its own, every site with a collector
serves a set of Lottage-only ingest points.** They're separate ports on the
site's existing addresses, one per stream Lottage's UniFi equipment sends (the
UDR alone sends syslog and CEF), and the collector stamps `lottage` on
everything arriving on them. Lottage's equipment sends to either site, and
which one is a UniFi setting rather than repo state. It can move without a
change here, if one site's path is better or the other is down. Serving them at
both sites costs a few idle listeners. Both sites list the same receiver, and
the site it stamps comes from an estate-wide variable, so `base/` never names
Lottage. Ports, not source-address matching, because a port is declared in one
place and doesn't drift with the network. The ingest points stand in for
Lottage's own collector and are removed when it exists. At that point
Lottage's equipment sends locally, like every other site's.

## Consequences

**Positive**

- No TSDB on an SD card; remote sites stay light.
- One place to query every site, and one place to trim cardinality.
- An outage within the window delays remote data instead of losing it.
- A new component is collected because it exists, so "is X monitored?" is
  answered by the cluster rather than by reading the collector's configuration.
- The collector's implementation can be reshaped (merged, split, replaced)
  without touching any sender or storage, as long as the contract holds.
- A signal's site comes from the ingest point, so a mobile sender's data says
  where it was without the sender knowing.

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
  and wherever the sender has stopped itself.
- **Every site runs every mobile sender's receivers.** They're the same OTLP
  receivers its own software uses, so this costs nothing until a mobile sender
  needs a protocol nothing else at the site does.
- **A site without a collector needs its site declared by hand**, as a set of
  ingest points of its own on every other site's addresses. Each stream it
  adds needs another port at every site, until it has a collector.
- **Every site spends a LAN address on syslog**, and keeps a DNS record for it,
  because the device's address only survives without a proxy.
- **A receiver's configuration can't be validated on its own**, only as part of
  a site's assembled configuration.
- **Push bursts and scraping share the forwarder's CPU and memory.**
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
- **An allowlist of which metrics to keep.** Needs knowing in advance which signals
  will be needed, and keeping up with what other people's software emits; both
  fail silently.
- **Our own software opting in.** We know what it emits, but two approaches are
  harder to reason about than one rule, and that knowledge does not need a
  different default.
- **Finding targets only by declaration** (the `prometheus.io/scrape`
  annotation, or #211). A declaration says where a target is, not which
  metrics to keep, so it is not an allowlist; but most charts here do not set
  one, so relying on it alone would miss them. It is kept as the workaround for
  a port the naming conventions miss.
- **Promtail for logs.** End-of-life, superseded by Alloy, which also collects
  metrics, so one collector covers both.
- **Apps pushing straight to Akron.** Works, but couples every app to a remote
  endpoint and loses the WAL, so a WAN outage becomes a hole in the data.
- **A mobile sender pushing straight to Akron's storage ingest.** The WAL
  argument above is weaker for a sender whose loss off the network is accepted
  anyway, but its data would skip the collector, where all trimming and
  labelling live.
- **A mobile sender always using Akron's collector.** It puts a site into the
  sender's configuration, and then either the sender has to report where it is
  or the data says Akron wherever it came from. The site-local name costs
  nothing, because every site already serves the receivers.
- **A forwarding-site label beside the site.** Every collector forwards to
  Akron, so it would repeat the site on almost every signal. Revisit it if
  forwarding becomes dynamic, for example with failover between collectors.
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
- **One collector process for everything.** A simpler model, but root would
  extend to the WAL and every network listener, and a log flood would share
  the reader's CPU with the WAL drain (#270).
- **A separate process for push receivers.** It keeps push traffic apart from
  scraping, but adds a second hop that needs a buffer of its own.
- **Syslog through a Traefik UDP entry point.** No extra address and no DNS
  record, but the proxy hides the device's address from the collector.
- **Sharing Traefik's address for syslog** (MetalLB `allow-shared-ip`). It ties
  the syslog Service to Traefik's Service and traffic policy, and gains nothing
  over an address of its own.
- **Moving the node log reader to a StatefulSet.** Only worth it for a single
  merged process with a WAL. The reader has nothing to persist.
- **Receivers enabled by flags in one base configuration.** Every site's
  receivers would live in `base/`, which is the site-specific knowledge base
  must not hold.

## Appendix: naming conventions

The conventions the collector translates to (see *Names and key values are
standardised in the collector*). The names chosen for each label, and the
lists of accepted values, are kept in the
[naming convention](../current-state/naming-convention.md).

- **One site label on every signal:** the site whose ingest point received it,
  or for the storage stack's own metrics, the site Prometheus runs at.
  Aggregations keep it, so a rule never merges sites.
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
