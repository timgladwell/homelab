# Planning a Workload's Observability

[ADR 0003](../adr/0003-observability-collection-and-storage.md) collects every
metric and log by default and drops only for the hardware's sake. So every
change that adds a workload or changes one, including a chart or image upgrade,
puts that workload's whole output on Akron's Prometheus and Loki the moment it
deploys. This is the research before that change and the check after it.

## Pre-checks

1. **What does it emit?** From upstream docs, the chart and the image: its
   metrics endpoints (port name and path), its log volume and format. For an
   upgrade, read the release notes for every version in the range for what
   changed: new metric families, renamed metrics, new labels, changed log
   verbosity.
2. **What will it cost?** Roughly how many series, and which families are
   expensive here. Histogram buckets and per-object labels are the usual ones.
   For logs, lines per minute at the default verbosity. Compare it with
   Akron's current `prometheus_tsdb_head_series`.
3. **No metrics endpoint?** That's a known gap. Say so in the PR, and decide
   there how to handle it.

## Steps

1. Write the estimate and the planned drops in the PR.
2. Add the drops in the collector, not the application, each with a `DROP` tag
   ([CONTRIBUTING.md](../../CONTRIBUTING.md#dropped-signals)). Silencing a line
   in the program also removes it from `kubectl logs`, which is the diagnostic
   surface: probe lines are what tell "the pod is wedged" from "nothing reaches
   it", and at sites that store no logs they are all there is. Scope each log
   drop by label to the workload measured to need it, not repo-wide. If the
   noisy request can be prevented instead (a favicon 404 fixed with an inline
   icon), do that.
3. **A new component also needs a scrape pair.** Collection is still an
   allowlist (#516), so a component is collected only if
   `base/metrics-collection/alloy-metrics.alloy` has a `discovery.relabel` and
   `prometheus.scrape` pair for it. Add one in the pattern of `cert_manager`:
   keep by namespace and metrics port name, and set any labels the component
   needs.

## Post-checks

After merge, at every site that runs it:

1. **Its signals arrive.** In Grafana, `up{job="<job>"}` is `1` per site, and
   its logs are in Loki under `{namespace="<namespace>"}`.
2. **They cost what pre-check 2 expected.** `scrape_samples_post_metric_relabeling{job="<job>"}`
   is close to the estimate, and Akron's `prometheus_tsdb_head_series` moved by
   about that much.
3. **Settle what is kept.** Anything larger than expected gets a `DROP`-tagged
   rule in a follow-up PR. Anything dropped that a question turns out to need
   is restored.
