# 8. What observability collects, and how its names are standardised

- **Status:** Proposed
- **Date:** 2026-11-06
- **Deciders:** Tim Gladwell

## Changelog

| Date | By | Description |
| --- | --- | --- |
| 2026-11-06 | Tim and Claude | Split out of [ADR 0003](0003-observability-storage-and-stack.md), unchanged in substance; that ADR's changelog holds the history before the split (#617) |
| 2026-11-06 | Tim and Claude | Standardise towards the names consumers already expect, renaming a sender's name only on a collision; drops checked against the rules and dashboards that read them (#624 review) |

## Context

[ADR 0003](0003-observability-storage-and-stack.md) decides where
observability data is stored and on what stack, and
[ADR 0009](0009-observability-ingest-contract.md) how it gets from a sender
to storage. This ADR decides what is collected: which signals, which are
dropped and why, and the form their names and values take in storage. It holds
whatever the stack is.

Constraints:

- **Most of what runs here is other people's software.** Its signals are
  defined upstream and change with each release.
- **Headroom.** Akron stores every site's data, and its CPU and memory are the
  scarce resource. A new workload's first intake can overwhelm Prometheus or
  Loki on this hardware.
- **Senders name things their own way.** OTLP metric names use dots and units,
  and some exporters spell site names their own way (Unpoller's `Akron`).

## Decision

**Every signal is collected by default; what is not needed is dropped.**
Metrics and logs from every workload at every site, ours or anyone else's, are
collected unless a drop excludes them:

- *Operating needs signals before the problem.* Which signal a problem needs is
  rarely known until it happens, so the default is all of them.
- *Most of what runs here is other people's software.* Tracking what every
  component emits in order to list it would be continual work, and would fall
  behind.

One rule covers everything, our own software included: a single approach is
easier to reason about than two.

**Collection is identical at every site**: hardware and node metrics,
Kubernetes metrics (kubelet, cAdvisor, kube-apiserver, kube-state-metrics, the
in-cluster exporters) and pod logs. Akron collects the same way; it is not a
special case.

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

**Drops, transforms and standardisation all live in the collector's
configuration**, in one place. That keeps one place to read for what storage
receives.

**Names and key values are standardised in the collector.** A sender's names
reach storage only after the collector has translated them to one convention.
That covers metric names arriving over OTLP, and label values such as site
names that a sender spells its own way. The conventions are common, published
ones rather than our own wherever one exists.

**Standardise towards what consumers already expect.** Rules and dashboards
from other people select on exact names: kube-prometheus-stack's default rules
and dashboards select on `job="kubelet"` and `metrics_path`, and read the node
from `instance`. So the collector reproduces those names rather than inventing
its own. Standardising decouples a consumer from each sender's spelling, but
every rename breaks every consumer of the old name. That trade is worth making,
because the packaged consumers here are few and replaceable. It is still made
only where it buys something:

- **A sender's own name is renamed only when it collides with ours or
  misleads.** Unpoller's `site_name` names the network it describes, not the
  site that received it, so it became `network_site`. The cost is stated beside
  the rename: here, every copied Unpoller dashboard was edited to match, and is
  edited again on each update.
- **A drop is checked against the rules and dashboards that read it.** A
  dropped signal breaks its consumers as silently as a rename does: a rule
  over a dropped histogram's buckets never fires.

The conventions:

- **One site label on every signal**, set as
  [ADR 0009](0009-observability-ingest-contract.md) describes. Aggregations keep
  it, so a rule never merges sites.
- **Key values are lowercase**, site names included (`akron`, not `Akron`),
  because lowercase keeps queries simple. The exception is a value that has to
  be camel case.
- **Accepted values are listed for reference, not enforced.** Developers read
  the list to plan the transformations they need. A new value turning up isn't
  a failure: it gets checked against these conventions and added to the list.
  The label names and the lists of accepted values are kept in the
  [naming convention](../current-state/naming-convention.md).
- **Metric names arriving over OTLP are translated in the collector** by the
  collector's own Prometheus exporter (dots to underscores, unit and `_total`
  suffixes), not by our own rules. That way, OTLP metrics take the same network
  path and the same buffer as every other metric.

## Consequences

**Positive**

- A new component is collected because it exists, so "is X monitored?" is
  answered by the cluster rather than by reading the collector's configuration.
- One place to trim cardinality and to read what storage receives.
- Every query and rule meets one convention, whatever the sender called it.

**Negative and risks**

- **More work per change.** Each change to a workload researches its
  observability ahead of time, re-checks production after it ships, and decides
  what is kept and dropped.
- **A new workload's first intake could overwhelm Prometheus or Loki** on this
  hardware, and become an incident. The research before it ships is the
  mitigation.
- **A rename costs every consumer of the old name**, including copies of
  third-party dashboards, which have to be edited again on each update.
- **A default only collects what is exposed.** A component with no metrics
  endpoint, such as PiHole with no exporter, stays dark; how to handle each is
  decided when its collection is implemented.

## Alternatives considered

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
- **Translating OTLP metric names in Prometheus's own OTLP receiver**, at
  Akron. It would send OTLP metrics around the collector's remote-write path
  and its WAL, so one kind of metric would travel and be buffered differently
  from the rest.
- **Scraping K3s's controller-manager and scheduler.** Embedded in the server
  binary on K3s; ~51K samples per scrape, of which ~724 series were useful (#51).
