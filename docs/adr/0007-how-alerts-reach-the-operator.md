# 7. How alerts reach the operator

- **Status:** Proposed
- **Date:** 2026-11-06
- **Deciders:** Tim Gladwell

## Changelog

| Date | By | Description |
| --- | --- | --- |
| 2026-11-06 | Tim and Claude | Proposed: Alertmanager routes every alert; an external heartbeat watches the path; push channel open (#617) |
| 2026-11-06 | Tim and Claude | Slack is the one push channel, Pushover the fallback; one heartbeat, from Akron (#214, #619) |

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
to a channel that doesn't depend on the estate. **There is one heartbeat, from
Akron**, because it watches the path and the path runs only at Akron. A remote
site going dark is an ordinary alert evaluated at Akron; what that misses is a
remote site failing while Akron is also down, which is learned when Akron
recovers.

**Slack is the one push channel**, for Alertmanager's notifications and the
heartbeat's alike. It is already on the operator's phone, so nothing else is
installed or kept running, and Alertmanager supports it natively. It honours
Do Not Disturb, which suits the constraint above: a `critical` alert such as a
site's WAN loss matters, but can't be acted on at night.

**Resolution is delivered wherever the alert was**, so `send_resolved` is set
explicitly on every receiver rather than left to per-integration defaults,
which differ.

### Open

These are decided in this ADR when they are chosen, not elsewhere:

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
- **Pushover**, for everything or for `critical` only. Per-message priority
  lets a `critical` override Do Not Disturb and repeat until acknowledged, and
  keeps alerts apart from other Slack traffic. Rejected for now: overriding Do
  Not Disturb isn't wanted, and it is one more app on the phone. It is the
  fallback if Slack is ever tuned out.
- **A heartbeat from every site.** It would report a remote site down while
  Akron is down too, a case UniFi's own WAN-loss notification mostly covers,
  for another check and another sender to keep.
- **Email.** Universal, but needs a relay and credentials, and it's the slowest
  channel to be noticed.
