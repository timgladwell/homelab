# 2. Metrics cover production by default; apps opt in

Date: 2026-09-21

## Status

Accepted.

Numbered 0002 because 0001 (`0001-postgresql-for-timbot.md`) is written but
unmerged on #355 at the time this was added.

## Context

Metrics collection at every site is configured in one file,
`base/metrics-collection/alloy-metrics.alloy`. It is an **allowlist**: one
`discovery.relabel` plus `prometheus.scrape` pair per target, each keeping pods
by specific labels. A workload with no stanza is collected nowhere, at any site.

That design has one failure mode and it is silent. Nothing reports that a
component is unmonitored — there is no error, no empty panel, no gap in a
dashboard nobody built. The question "is X monitored?" can only be answered by
reading this file, and the answer is assumed rather than checked.

It cost real time. Akron's cert-manager webhook crash-looped for 17 days
(#323), and diagnosing it stalled because `go_goroutines` and
`go_memstats_heap_inuse_bytes` were not collected. Every figure available came
from cAdvisor, which sees how much memory a container holds and nothing about
what is in it, so three successive hypotheses were argued from container
metrics and each turned out wrong. cert-manager had never had a stanza. Nothing
had ever said so.

Traefik, MetalLB, PiHole's FTL and the Flux controllers are in the same
position today.

## Decision

**Every production workload is collected at every site, by default.**
Production means every layer except `apps`: `infrastructure`,
`infrastructure-config`, `dns-config` and `monitoring`. In namespace terms that
is everything except the namespaces the `apps` layer owns.

**Apps opt in individually.** Not because app metrics are unwanted — they are
often wanted early, since developing anything without observability is
painful — but because an app's namespace is where short-lived and experimental
workloads live, and defaulting them in makes the collector's cost follow
someone's scratch deployment.

Two consequences follow, and both are the point:

- A new production component is collected **because it exists**, not because
  someone remembered a stanza. This matches how the rest of the repo already
  works: a remote site remote-writes correctly by existing, because the
  endpoints are defaults in `clusters/common/network-vars.yaml`.
- The failure mode inverts. Previously a missing target was invisible;
  now an unwanted target is *visible*, as series that show up and can be
  excluded deliberately. A loud wrong answer beats a silent one.

This applies to every site equally. Collection has always been identical
everywhere — only *storage* is Akron-only — and nothing here changes that.
Eastbank's observability differs from Akron's today in what it stores and in
running no log-collecting DaemonSet; it should not differ in what it collects.

## Consequences

**Coverage is no longer the same thing as collection.** A namespace rule makes
a workload's metrics endpoint get scraped if it has one. Several production
components do not: Traefik ships no `metrics.prometheus` block, PiHole has no
exporter at all. Those are separate pieces of work and this decision does not
complete them — it means their absence is now a known gap rather than an
unexamined one. Tracked under #221.

**Cardinality is a real cost on this hardware and is not addressed here.**
Collecting more by default means more series, on a box where the kubelet and
apiserver histogram buckets already had to be dropped wholesale. The namespace
rule must be paired with the existing drop rules, and a component that turns
out to be expensive gets a targeted drop rather than removal from collection.

**Stanzas with custom relabeling still exist.** node-exporter sets `instance`
to the node name, kube-state-metrics to a fixed string, cert-manager to the
component. A generic rule cannot produce those, so specific stanzas remain
where the labels matter, and the generic rule must not double-collect what they
already cover.

**`alloy-metrics.alloy` becomes the wrong place to ask "what is monitored?"**
Under an allowlist the file was the answer. Under a default it only records the
exceptions, and the answer moves to the cluster — which is what having the
metrics is for.

## Alternatives considered

**Keep the allowlist and add stanzas as gaps are found.** This is the status
quo and is what produced #323's dead end. It works exactly until someone does
not notice, and noticing is the part that fails.

**Pod annotations (`prometheus.io/scrape`).** The classic pattern, and it
inverts the same way, but it puts the decision in each workload's manifest —
including inside Helm charts this repo does not own, where setting an
annotation means a values override per chart. It also spreads the answer across
the tree instead of keeping it in one file. #211 covers the related idea of
components declaring their own metrics; that is a bigger change than this.

**Scrape everything, with no namespace rule.** Simpler still, and it puts
experimental app workloads in the collector's path permanently. The `apps`
boundary is the one distinction worth encoding.
