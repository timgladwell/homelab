# 9. How observability data gets from a sender to storage: the ingest contract

- **Status:** Proposed
- **Date:** 2026-11-06
- **Deciders:** Tim Gladwell

## Changelog

| Date | By | Description |
| --- | --- | --- |
| 2026-11-06 | Tim and Claude | Split out of [ADR 0003](0003-observability-storage-and-stack.md), unchanged in substance; that ADR's changelog holds the history before the split (#617) |
| 2026-11-06 | Tim and Claude | Syslog ingest only takes lines raw; what a parser keeps, and how its health is measured, moves to ADR 0008 (#624 review) |
| 2026-11-06 | Tim and Claude | An exporter is a sender outside the domain, placed by headroom; Unpoller's placement moves here from ADR 0003 as the example (#624 review) |

## Context

[ADR 0003](0003-observability-storage-and-stack.md) decides where
observability data is stored and on what stack, and
[ADR 0008](0008-what-observability-collects.md) what is collected. This ADR
decides the path between them: what a sender depends on to deliver its data,
how that data reaches storage, and what every signal carries when it arrives.
It holds whatever the stack is.

Constraints:

- **Network.** Sites are joined by a site-to-site VPN that can go down. PiHole is
  both each site's resolver and one of the things being monitored, so telemetry
  must not depend on it. Some links are metered and slow, such as Lottage's.
- **Senders.** Most telemetry comes from equipment that stays at one site: its
  cluster's workloads and nodes, and devices on its LAN such as the UDR,
  printers, desktop computers and landline phones, some of which speak only UDP
  syslog. Some comes from mobile equipment that roams between sites and off the
  network, such as laptops, tablets and mobile phones.
- **Not every site runs a collector.** Lottage's server isn't managed by this
  repo.

## Decision

**Each site has one logical collector, defined by its contract.** Everything a
site observes is pulled into it or pushed at it, and it forwards everything to
Akron. Its contract is what senders and storage depend on: the push APIs and
receivers it serves, the labels it guarantees on every signal, and where it
forwards. That may be several APIs and several scrapers. How many processes
run behind it, and of what kind, is not part of the contract. That changes
without either side changing, and a change to the contract changes both sides
together.

**Observability is one domain, and its interface is each site's ingest
points.** Every site's collector, and the storage, Grafana and Alertmanager
that run only at Akron ([ADR 0003](0003-observability-storage-and-stack.md)),
form a single observability domain. Each site publishes ingest points: to its local
network, such as syslog and OTLP over HTTP, and to its own cluster, an internal
collector service. Senders depend on those and on nothing else. Data moving
east-west between sites stays inside the domain: a remote site's collector
writes straight to Akron's storage over the VPN, and does not go back out
through Akron's ingest points, any more than a call within a module goes back
out through the module's interface. Nothing outside the domain depends on how
it works inside; within it, that restriction does not apply.

**The collector is the only thing at a site that crosses the VPN.** Akron's
storage ingest endpoints are path-scoped Traefik routes on `websecure`
(`/api/v1/write`, `/loki/api/v1/push`), with the hostnames pinned in the
collector's `/etc/hosts` at remote sites, so no resolver is in the path.

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
with no collector of its own sends to another site's collector. Senders send
straight to a collector, unbuffered; where buffering starts is
[ADR 0003](0003-observability-storage-and-stack.md)'s.

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
would only repeat the site. The storage stack's own metrics carry the site
Prometheus runs at ([ADR 0003](0003-observability-storage-and-stack.md)).

**An exporter is a sender, outside the domain.** Equipment that can't send its
own telemetry (a UniFi controller, a node's kernel, the Kubernetes API) is made
observable by an exporter that translates it. The exporter depends on the
ingest points like any other sender, and is scraped or pushes through its
local collector. It runs wherever there is headroom, not necessarily beside
what it observes or beside storage. Unpoller is the example: one instance at
Eastbank polls every site's UniFi controller over the VPN and sends through
Eastbank's collector (#120), because polling doesn't need to sit next to
storage and Akron's headroom is what is scarce. The site label still follows
the ingest point, so what the data describes goes in a label of its own
(`network_site`, [ADR 0008](0008-what-observability-collects.md)).

### The ingest points

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

**UDP syslog gets its own LAN address at each site**, a LoadBalancer Service
selecting the collector, and a DNS record. This is an exception to reaching
the collector through Traefik. Traefik can carry UDP on its existing address,
but it proxies the datagram, so the collector would see Traefik as the sender
and lose the device's address. For devices that leave the syslog hostname
empty, that address is the only host label there is. With
`externalTrafficPolicy: Local` on its own Service, the address reaches the
collector. TLS syslog is added beside it if a sender turns out to support it.

**Syslog ingest takes every line raw; parsing comes after it.** A receiver
that parses as it receives discards whatever it can't read: Akron's strict RFC
3164 receiver dropped 145 of about 352,000 UniFi lines in a week, and those
lines could never be queried. So the receiver accepts each line as it arrives,
and parsing is a separate step behind it. What a parser keeps or drops, and
how its health is measured, is
[ADR 0008](0008-what-observability-collects.md)'s.

**Sites opt into receivers; base never knows which.** Each reusable receiver,
such as UniFi's syslog parsing, is a Kustomize Component in `base/`. It adds
one file to the collector's configuration and its ports on the Service that
exposes it, and a site lists the Components it runs. The syslog address is
shared by every syslog sender at a site, so it is a Component of its own,
listed before the senders. Each sender adds its ports to it, and none owns it.
The second sender, the OBi100, showed this: the address had been part of
UniFi's receiver, and adding a second sender meant splitting it out into a
generic syslog Component that UniFi's and the OBi100's receivers both add to
(#596).
The collector loads its configuration as a directory, so a receiver's file
joins the base file without editing it. A receiver depends on a stable entry
point that the base file always defines, and the base file depends on no
receiver, which keeps the layering direction
[ADR 0004](0004-repository-layering.md) requires. A receiver's file isn't
valid on its own, so validation checks each site's assembled configuration,
not the files one at a time.

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

- The collector's implementation can be reshaped (merged, split, replaced)
  without touching any sender or storage, as long as the contract holds.
- A signal's site comes from the ingest point, so a mobile sender's data says
  where it was without the sender knowing.
- Telemetry doesn't depend on any site's resolver.

**Negative and risks**

- Remote telemetry depends on Akron's Traefik, and each site's LAN devices
  depend on their own site's Traefik.
- **Plaintext syslog can be forged** by anyone on the VLAN, and any sender can
  push to an ingest endpoint unless it authenticates.
- **A device's data is lost while its collector is unreachable.** Lottage's
  devices reach theirs across the VPN, so any VPN outage loses their data.
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

## Alternatives considered

- **Apps pushing straight to Akron.** Works, but couples every app to a remote
  endpoint and loses the collector's buffer, so a WAN outage becomes a hole in
  the data.
- **A mobile sender pushing straight to Akron's storage ingest.** The buffering
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
- **Authenticating senders with mTLS.** Deferred since #228. Every endpoint is
  internal only, every site is one node, and the concern is data in clear,
  which TLS already covers. A site with a second node, or an endpoint reachable
  beyond the LAN and VPN, reopens it.
  <!-- ASSUMES(single-node): one of the reasons mTLS is deferred. -->
- **Telemetry endpoints by IP**, to avoid DNS. Breaks TLS: Traefik picks the
  certificate from SNI, which an IP does not carry. `/etc/hosts` pinning gets
  the same independence from DNS.
- **Unpoller at Akron, one instance per controller.** The original layout,
  replaced to save Akron's headroom (#120).
- **Syslog through a Traefik UDP entry point.** No extra address and no DNS
  record, but the proxy hides the device's address from the collector.
- **Sharing Traefik's address for syslog** (MetalLB `allow-shared-ip`). It ties
  the syslog Service to Traefik's Service and traffic policy, and gains nothing
  over an address of its own.
- **Receivers enabled by flags in one base configuration.** Every site's
  receivers would live in `base/`, which is the site-specific knowledge base
  must not hold.
