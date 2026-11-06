# 6. How observability is used: what alerts, at what level, and what it tells the operator

- **Status:** Proposed
- **Date:** 2026-11-06
- **Deciders:** Tim Gladwell

## Changelog

| Date | By | Description |
| --- | --- | --- |
| 2026-11-06 | Tim and Claude | Proposed: two severity scales, alert levels decoupled from delivery, exhaustion alerts alongside SLOs (#617) |

## Context

[ADR 0008](0008-what-observability-collects.md),
[ADR 0009](0009-observability-ingest-contract.md) and
[ADR 0003](0003-observability-storage-and-stack.md) decide what observability
data is collected, how it reaches storage, and where it is stored. This ADR
decides what is done with it: what becomes an alert, what an alert's level means, what an alert must say,
and what is left to dashboards.

Constraints:

- **One operator, who is also the first customer.** Nobody watches the system
  full time. The operator expects it to say when it is unhealthy, because
  otherwise it has to be watched. The household is the second customer: its
  DNS, network and the services the estate hosts.
- **No margin to absorb failure.** Each site is one Raspberry Pi 4B. A node
  can't fail out of a cluster, storage isn't separate from the node or the
  control plane, and memory and CPU run close to their limits. A behaviour
  change in resource use can't be ignored until it causes symptoms, because
  there's nothing to fail over to when it does.
- **Slow-moving exhaustion ends abruptly.** A filling disk, a growing buffer or
  shrinking headroom has no user-visible symptom until the day the system stops.
  The operator doesn't have the bandwidth to keep checking slow-moving
  resources by eye.
- **The stack already assumes alert level names.** kube-prometheus-stack's
  default rules carry a `severity` label: of the 128 alerting rules running
  here, 28 are `critical`, 91 `warning`, 7 `info` and 2 `none` (*measured*
  2026-11-06). The chart's Alertmanager inhibit rules match on those same
  values. The definitions come from
  [kubernetes-mixin](https://github.com/kubernetes-sigs/kubernetes-mixin)'s
  *Alert Severities*.
- **Logs arrive with whatever levels each source uses.** Grafana's log levels
  are in use, and the UniFi receiver normalises CEF severity onto them as a
  `level` label. Other sources aren't normalised yet.

This isn't new ground. The research behind this decision, with verbatim sources
and what could not be verified, is on #617 (comment of 2026-11-06).

## Decision

**These are defaults, not absolutes.** Where a case needs to depart from one,
the departure is stated, with its reason, beside the rule or panel that makes
it.

### Two scales: what happened, and what it needs

**Event severity** describes something that happened: a log line or an event.
Every source is normalised onto one scale, Grafana's log levels, defined by
impact:

| Level | Meaning |
| --- | --- |
| `debug` | Detail for diagnosing a component; not kept by default. |
| `info` | Expected, worth recording. A deploy, a restart on schedule. |
| `warning` | Unusual but not a failure yet: something elevated, a retry that succeeded. |
| `error` | An unexpected failure confined to one operation: one request, one job run. No impact beyond it. |
| `critical` | A failure with impact beyond one operation: many requests, lost or unrecoverable data, a component down. |
| `unknown` | The source gave no level, or one that can't be mapped. It's an explicit value, so a gap in normalisation is visible rather than silently becoming `info`. |

Normalising happens in the collector, with the other standardisation
[ADR 0008](0008-what-observability-collects.md) puts there. A source
whose level has to be read from the content of each line (as syslog parsers
do) maps what it can and leaves the rest `unknown`.

**Alert level** describes the response an alert needs. It uses the values the
stack already assumes (Context), defined by what the operator must do:

| Level | Meaning |
| --- | --- |
| `critical` | Act as soon as you can. Something has failed, or will fail soon enough that waiting is the failure. |
| `warning` | Act, but on your own schedule, within days. Something is degrading or heading for a limit with time to spare. |
| `info` | No action on its own. Context that explains other alerts or helps troubleshooting. |
| `none` | Machinery: alerts that exist to drive Alertmanager or the dead-man's switch, never shown as a problem. |

**A rule connects the two**, and chooses the alert's level by the response
needed, not by the events' levels. Usually it fires on a condition over events
or metrics, such as an error rate above a threshold for long enough; one failed
request isn't an alert. **A single event is an alert** when it is the only
evidence of a failure that needs action: software that reports "transform
failed" once, as a log line, with no status to poll afterwards.

### Alert levels are decoupled from delivery

An alert's level states its priority and the response it needs. **How** and
**when** it reaches the operator (which channel, whether it batches, quiet
hours, whether it ever wakes anyone) is routing policy. Routing is configured
separately and can change without relabelling any alert.

So no rule is written to suit a channel, and no level is redefined because a
channel is noisy. Being woken at night is a delivery choice, not a level. A
`critical` alert is critical even if it waits until morning.

Illustration only, nothing depends on it: `critical` delivered promptly, `warning`
batched, `info` not delivered and seen on dashboards. How alerts reach the
operator is [ADR 0007](0007-how-alerts-reach-the-operator.md).

### What earns an alert

An alert needs an action (*Every alert is specific and actionable*, below).
Three kinds qualify:

1. **Service levels.** A customer-facing symptom against an objective. This is
   the primary kind. Household DNS is an example: query volume, latency, and
   blocked against allowed. The operator is a customer too, and
   "the system tells me when it is unhealthy" is an objective of its own: a
   site, a signal or the alerting path going silent is an alert.
2. **Exhaustion.** A finite resource heading for a limit, where nothing is
   degraded until it is suddenly gone: storage, buffers, memory headroom,
   quotas, certificate and token expiry. This includes headroom shrinking over
   time, not only a fixed threshold. These are alerts here even though nothing
   user-visible precedes them, because the constraints above leave no failover
   and no attention to spare.
3. **Behaviour change.** A workload's resource use departing from its
   established behaviour (restarts, refaults, throttling, growth) on hardware
   with no slack to absorb it.

What does not earn an alert:

- **A condition with no action.** It goes on a dashboard.
- **An event to record**, such as a deploy. It is a dashboard annotation, not an
  `info` alert.
- **A single failure** with no impact beyond itself. It's an `error` event,
  counted by a rule if its rate matters.

### Every alert is specific and actionable

An alert names the exact thing, the condition and the value, so the operator
can act without first investigating what it means. "A container is using a lot
of resources" fails this test. "akron's root filesystem is 92% full" passes.
Where a trend makes it possible, the alert also says when the limit is reached
("…and fills in 4 days at the current rate"). Many signals won't support that,
and the current value is enough.

- **Labels identify the object**: `site` always, then whatever identifies it
  (namespace, workload, pod, container, PVC, device).
- **`summary`** states object, condition and value in one line.
- **`description`** states the impact, and what happens if it is ignored.
- **A link to where to start**: a runbook, or the detail dashboard showing the
  object.

The chart's default rules are held to the same test. A default that fails it is
fixed or disabled, not tolerated.

### Lifecycle

- **Persistence before firing, by default.** A condition must last long
  enough to be real (`for:`), sized to the condition, so a blip is not an
  alert. The exception is a one-shot event that is the only evidence of a
  failure (*A rule connects the two*), which fires as soon as it is seen.
- **Recovery is notified.** Any alert that was delivered also delivers its
  resolution.
- **Flapping is damped**, so an alert that clears and returns within a short
  window stays one alert (`keep_firing_for`).
- **The alerting path is watched from outside the estate.** A heartbeat leaves
  the estate continuously, and its absence alerts from a service that doesn't
  depend on the estate. Nothing inside can report that Akron, Prometheus or
  Alertmanager has stopped.

The values (durations, thresholds, intervals) live in the rules and routing,
with their reasoning beside them.

### Counting failures

Until an objective exists to measure against, a rate or ratio over a window,
held for a duration, is the form. Once a service level has an objective,
burn-rate alerting against it (the SRE workbook's multi-window, multi-burn-rate
approach) replaces raw thresholds for that service.

### Dashboards carry everything that isn't an alert

Dashboards are where the operator looks when no alert has fired, or when one
has and needs context:

- **A hierarchy: summary, detail, most detailed.** The summary answers "is
  anything wrong" at a glance, and is cheap, because almost always nothing is.
  Detail dashboards are for understanding and resolving a problem, and that is
  where expensive queries belong, reached by a link or a collapsed row, not
  run every time the summary loads.
- **Every dashboard shows deploys**, as annotations, with a shared crosshair, so
  a change can be correlated with any signal.
- **Dashboards select KPIs.** A dashboard's value is the signals it chose. One
  that shows everything moves the search from Explore to the dashboard.
- **Alerts link into them.** The detail dashboard for an alert's object is its
  default starting point.

## Consequences

**Positive**

- The default rules' levels mean what this ADR says. Rules and dashboards share
  one vocabulary with no remapping.
- Delivery can be changed (channel, quiet hours, batching) without editing a
  rule.
- Exhaustion and behaviour-change alerts cover what a symptom-only approach
  would miss on hardware with no margin.
- An alert that can't name its object and action is rejected at review, so
  noise is stopped at the rule rather than at the operator.

**Negative and risks**

- **More rules than a symptom-only design.** Exhaustion and behaviour-change
  alerts are cause-based, which the sources warn tends towards noise. The
  specific-and-actionable test is the guard, and it must actually be applied.
- **Behaviour change needs a baseline**, and a baseline needs history. A new
  workload has none, so its behaviour alerts can't exist on day one.
- **The service-level objectives can't be measured yet.** DNS volume, latency
  and block rate aren't collected today, so the primary kind of alert starts
  empty and the other two carry the load until it isn't.
- **Two scales with overlapping names** (`critical`, `warning`, `info` on
  both) can be confused. The label distinguishes them: `level` on events,
  `severity` on alerts. Distinct names (`sev1`…) were weighed and rejected
  (*Alternatives*), because the stack assumes these.
- **Normalising every source's levels is work per source**, and until it is
  done a source's lines are `unknown` or carry its own vocabulary.
- **Decoupled delivery can hide a gap.** A `critical` alert routed to a channel
  nobody reads is still undelivered. Routing has to be reviewed against the
  levels whenever either changes.

## Alternatives considered

- **One scale for events and alerts**, with an alert's level taken from the
  severity of what happened. Rejected: impact and required response are
  different questions. A `critical` event (a component down at 3am, recovered
  by itself) may need no action, and a quiet exhaustion trend needs action with
  no event at all. It would also mean relabelling every default rule.
  PagerDuty keeps alert severity, notification urgency and incident priority
  apart for the same reason.
- **Levels defined by channel**, as the mixins describe them (`critical` to a
  pager, `warning` to a ticket queue). Rejected: it couples the meaning of an
  alert to delivery, and there is no pager or ticket queue here.
- **Symptom-only alerting**, as the Google SRE book and the Prometheus docs
  recommend. Rejected as the whole answer, kept as the primary kind. Those
  sources assume redundancy, failover and people watching dashboards; this
  estate has none of them, and its most likely failures are silent
  exhaustion.
- **`sev1`, `sev2`… for alert levels**, as incident-severity scales name
  them. They describe much the same thing (scope, impact and response
  needed), and distinct names would keep alerts and events apart at a glance.
  Rejected because the stack assumes `critical`/`warning`/`info`: 128 default
  rules carry them and the chart's inhibit rules match on them. Prometheus's
  alert relabelling could rewrite them, but then every default, inhibit rule
  and runbook disagrees with what the operator sees. We don't fight the
  stack's defaults for a naming preference.
- **Burn-rate alerting from the start.** Deferred, not rejected: it needs
  service-level measurements that don't exist yet.
