# 7. How alerts reach the operator

- **Status:** Proposed
- **Date:** 2026-11-06
- **Deciders:** Tim Gladwell

## Changelog

| Date | By | Description |
| --- | --- | --- |
| 2026-11-06 | Tim and Claude | Proposed: Alertmanager routes every alert; an external heartbeat watches the path; push channel open (#617) |

## Context

[ADR 0006](0006-how-observability-is-used.md) decides what alerts and at what
level, and keeps levels separate from delivery. This ADR is the delivery: the
products and channels that carry an alert from the rule that fires it to the
operator, in the way [ADR 0003](0003-observability-storage-and-stack.md)
chooses the stack that carries data to storage.

Constraints:

- **One operator, one phone.** Nothing from the estate needs to wake the
  operator. What does at night is a life-safety or security event (fire, a break-in)
  from products that notify on their own, not from this estate's alerts.
- **Everything that evaluates rules runs at Akron**, so an alert about Akron,
  or about the path itself, can't be delivered by that path.
- **Alertmanager is already deployed** by kube-prometheus-stack, with the
  chart's inhibit rules and a `null` receiver (*measured* 2026-11-06).
  Prometheus evaluates every rule today. Rules over logs need an evaluator
  that doesn't exist yet.
- **A notifier hosted on the estate can't report the estate down.**

## Decision

**Alertmanager is the one router.** Every alert reaches the operator through
it, whatever evaluates the rule: Prometheus for metrics, and whatever evaluates
log rules (*Open*), configured to send to the same Alertmanager. Routing,
grouping, inhibition, silences and quiet hours live in one place, keyed on
ADR 0006's `severity`. The chart's inhibit rules are kept.

**The alerting path is watched from outside the estate.** The chart's
always-firing `Watchdog` alert is routed to **healthchecks.io**, which notifies
when the heartbeat stops. That covers what nothing inside can report: Akron,
Prometheus or Alertmanager down, or Akron's WAN out. Its own notification goes
to a channel that doesn't depend on the estate.

**Resolution is delivered wherever the alert was**, so `send_resolved` is set
explicitly on every receiver rather than left to per-integration defaults,
which differ.

### Open

These are decided in this ADR when they are chosen, not elsewhere:

- **The push channel.**
  - **Slack:** already on the operator's phone; native in Alertmanager
    (`slack_configs`, webhook URL as a SOPS secret).
  - **Pushover:** per-message priority, so a `critical` can override Do Not
    Disturb and repeat until acknowledged, and it stays apart from other Slack
    traffic.
  - A middle option: Slack for everything, adding Pushover for `critical` only
    if Slack is ever tuned out.
- **Where log rules evaluate**: Loki's ruler, sending to Alertmanager, or
  Grafana-managed alerting, sending to the same Alertmanager.

## Consequences

**Positive**

- One place to read and change delivery, and every alert gets the same
  inhibition and silencing regardless of what evaluated it.
- The path's own failure is detected, by something that survives it.

**Negative and risks**

- **A third party sits on the critical path** for the dead-man's switch and,
  depending on the channel, for delivery too. Its account and configuration
  live outside git ([State That Is Not In Git](../current-state/host-state.md)).
- **Alertmanager at Akron is a single point of delivery.** While Akron is
  down, every site's alerts wait. The heartbeat says that it's down, not what
  else is wrong.

## Alternatives considered

- **Grafana-managed alerting as the router.** Grafana has its own
  Alertmanager-compatible router. Rejected: the default rules, their inhibit
  rules and their routing all live in Prometheus and Alertmanager, so moving
  routing to Grafana would split it in two. Grafana may still *evaluate* log
  rules (*Open*), sending them here.
- **A self-hosted notifier** (ntfy, Gotify). It runs on the estate it reports
  on, so it fails with it; a site-down alert needs a path that doesn't.
- **Email.** Universal, but needs a relay and credentials, and it's the slowest
  channel to be noticed.
