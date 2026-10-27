# 5. How the system integrates third-party products

- **Status:** Proposed
- **Date:** 2026-10-27
- **Deciders:** Tim Gladwell

## Changelog

| Date | By | Description |
| --- | --- | --- |
| 2026-10-27 | Tim and Claude | Proposed: the vendor's own integration first; improvements tracked here (#505) |

## Context

Almost everything the system runs is someone else's product: Flux, the Helm
charts, Grafana, Prometheus, Loki, Pi-hole, UniFi and its exporters. Each needs
connecting to something else — a person querying it, a secret reaching its
config, a value kept in step with another — and there are two ways to do it:
the product's own mechanism, or glue written here.

Constraints:

- **One maintainer.** Every script or wrapper written here is maintained here,
  through every upstream release that changes what it wraps.
- **Least privilege.** An integration's access has to be scoped (CLAUDE.md,
  *Security by default*), and a scope built here is one more thing to get right.
- **Upstreams vary.** Some are well-funded vendors; some exporters and images
  are one person's side project, and some of those are abandoned.

## Decision

**Use the product's own integration before writing glue.** Before designing a
script, wrapper or runbook of `curl` calls against a product, look for what the
product ships: an MCP server, a CLI, a chart option, an operator, an exporter.
Glue is written only when none exists, and then it is this repo's code, held to
the same standards as everything else.

A chart's own option is the same rule at the small scale: an existing-secret or
checksum option the chart provides beats an annotation kept in step by hand
(CONTRIBUTING, *Weighing the alternatives*).

**Improvements to a third-party product are tracked in this repo**, with the
upstream repository and its issues cited as prior art, not filed upstream. An
upstream may be abandoned, and the intent for a product this system depends on
is a maintained fork or a replacement, which is this repo's work. Asking an
upstream something publicly is still possible, as a deliberate step of that
work.

## Consequences

**Positive**

- The vendor maintains the integration across its own releases, and usually
  ships a security posture with it: `mcp-grafana --disable-write` drops every
  mutating tool from what it advertises, which a Viewer-scoped service account
  then backs (#220).
- Results come back structured, not scraped out of HTTP responses.
- The backlog for a depended-on product is in one place, whatever the upstream's
  state.

**Negative and risks**

- The vendor's integration decides the shape: a feature it lacks is missing
  until it ships, or until glue is justified.
- One more dependency to pin and upgrade. It is cheaper than code written here,
  but not free.
- Tracking improvements here duplicates upstream issues that are alive; the
  citation keeps the two connected.

## Alternatives considered

- **Custom glue first.** Rejected. #220 planned `scripts/grafana-query.sh` and a
  runbook of datasource-proxy `curl` calls; Grafana's own MCP server replaced all
  of it, with a better security posture, before any of it was built.
- **File improvements upstream.** Rejected as the default. An issue on an
  abandoned repository goes nowhere and leaves nothing in this repo's backlog.
