---
name: review-deps
description: Review the open dependency bump PRs (Renovate and Dependabot) — gather with scripts and Haiku, judge only what needs it, and post each verdict with a merge plan on its PR. Use when asked to review dependency bumps, Renovate PRs or the weekend's updates.
---

<!--
#641. Three tiers, cheapest first: scripts/dep-review.sh collects what GitHub
already has (no tokens), the dep-triage agent (Haiku) gathers release notes
and quotes leads, and this session judges only the PRs the gate sends it.
The logic is in the script and the agent's prompt so CI (#642) can run the
same pieces.
-->

## 1. List

`scripts/dep-review.sh` prints the open PRs labelled `dependencies`. If it
refuses because the checkout is behind `origin/main`, stop and pass its
message to Tim; don't pull for him, and don't review from the stale checkout.

## 2. Triage, in parallel

Spawn one `dep-triage` agent per PR, all in one message. Give each only its
PR number. Note each agent's token count from its completion notice, for the
cost line in step 5.

## 3. Gate

A PR goes to judging if **any** of these hold; otherwise it is mechanical:

- major update, or an update to k3s or Flux;
- any CI check failed, or flate failed to render;
- rendered changes beyond version strings and image tags;
- a lead that says breaking, removed, renamed, migration or required action,
  or touches CRDs;
- a coverage gap the triage could not fill, or the packet's ⚠ NO RENDERED
  DIFF;
- a runbook named after the dependency.

**GitHub Actions bumps** (the PR only touches `.github/`) render nothing, so
"rendered changes" never applies to them. They gate on a major version (a new
Node runtime, or changed or removed inputs) or on a gating lead. When judged,
check every `with:` input and `steps.*.outputs` reference the workflows use
against the new version, and whether its required `permissions:` changed.

Changed defaults, new options and security fixes do not gate on their own:
Haiku quotes some lead for every PR, so "any lead" sent all seven of the
first run to judging. A changed default that matters shows up in the
rendered diff; one that doesn't render is for a component this repo
disables.

A mechanical PR is reported as ready to merge, with the evidence that made it
mechanical. Nothing else is done for it.

## 4. Judge

Treat every triage lead as unverified. For each gated PR:

- **Verify leads at source.** Open each quoted URL (or `gh release view`)
  and confirm the quote says what the triage claimed and is for this
  dependency. Mark it verified, contradicted, or unverified (could not read).
- **Check it against this repo.** Read the touched file and the values it
  sets: is a renamed or removed value set here? Grep for renamed metrics or
  labels in dashboards, alert rules and the collector. Does a new image
  support ARM64? Does a CRD change need [Helm Chart Upgrades with CRD
  Changes](../../../docs/runbooks/helm-crd-upgrades.md)? Does the change hit
  anything [Planning a Workload's
  Observability](../../../docs/runbooks/workload-observability.md) covers?
- **Read rendered changes** that are not version strings: a new default
  (probe port, `defaultMode`, `revisionHistoryLimit`) is a change the release
  notes may not mention.
- **Follow a named runbook's pre-checks**, and list its manual steps.
- **A failed render or Validate** is the first thing to explain; the rest of
  the review waits on it.

## 5. Plan the merges

Tim merges from the PR comments over days, often distracted, so the plan is
written for someone who has none of this conversation. Plan across every PR
in the run, mechanical ones included.

**Why order matters.** Promotion fast-forwards `stable` to a commit on `main`
([Promoting to `stable`](../../../docs/runbooks/promote-to-stable.md)), so
everything merged so far reaches Eastbank together. A bad merge blocks
promotion of everything after it until it is reverted. So a batch is merged
only once the previous batch is healthy at Akron.

**Batches.** A PR goes in a batch of its own when it:

- restarts the node or every pod: k3s, Flux, system-upgrade-controller;
- has a manual step before merging (a CRD apply), so the apply and the
  rollout show up separately on the dashboards;
- is a major version.

Otherwise PRs share a batch, with **at most one** from these two groups per
batch:

- the observability plane (the collector, Loki, kube-prometheus-stack,
  kube-state-metrics). Every other change is checked through it, so a
  broken signal must be attributable to one change;
- the access path (Traefik, MetalLB, Pi-hole). If it breaks, Grafana is out
  of reach for checking anything else
  ([Reaching PiHole When Traefik Is Not Routing](../../../docs/runbooks/pihole-access.md)).

PRs touching the same file never share a batch: the second needs Renovate's
rebase and a fresh review.

**Order.** Security fixes first, then the observability plane (so later
checks run on a plane already shown good), then the access path, then the
rest, and node-level changes last. Held PRs are left out. Say which batches
can be promoted together, and that a held PR must not be merged before a
promotion.

**Watch time** scales with blast radius (CONTRIBUTING, change period): a
glance at Flux Ready and restarts for a patch that renders only a version, a
restart or two of the observability plane's own signals for a change to it.

## 6. Post on each PR, then report here

Every PR in the run gets a comment, mechanical and held ones included. Write
it to a file and post with `scripts/dep-review.sh post <pr> <file>`, which
replaces an earlier review comment instead of stacking a second one. The
head SHA is the packet's `<!-- head: … -->` line.

```markdown
🤖 *Posted by Claude via @timgladwell's account.*
<!-- review-deps -->
## Dependency review: <verdict> · batch <n> of <N> · <alone | with #x>

Reviewed at <sha7> on <date>. **If this PR's head has moved since (a
Renovate rebase or a newer version), this review is stale: re-run
`/review-deps`.**

### Merge plan
<one line why this batch: the rule above that applies>. <Merge after batch
n-1 is healthy at Akron | Merge first>. <Promote with … | Akron-only:
Eastbank renders no change, so promotion is a formality for this PR>.

| Batch | PR | Dependency | Verdict |
|---|---|---|---|
| 1 | #<a> | <name old → new> | <verdict> |
| **<n>** | **this PR** | **<name old → new>** | **<verdict>** |
| — | #<h> | <name old → new> | hold: <three words why> |

<The whole run, one row per PR, in merge order with held PRs last; this
PR's row in bold. Identical in every comment except the bold row, so any
comment shows what comes next.>

### Before merging
<manual steps, with commands | Nothing.>

### After merging, at Akron
<checks for Tim to run, scaled to blast radius; what "healthy" looks like>

### Why
<2–5 bullets: the deciding evidence, with links>

<details><summary>Leads checked</summary>

<each lead: verified / contradicted / unverified, with its source>

</details>
```

A held PR's heading reads `hold · not batched`. Its comment says what would unblock it and that it must not be
merged before the next promotion. A **needs Tim** comment states the
decision as a question.

Then report here: one table of PR, verdict and batch, with a link to each
comment. End with a cost line (PRs total, PRs judged, triage tokens per agent
and in total) and append it to #641 while the process is being measured.

## Boundaries

The review comment above is posted by default. Do not merge, approve or
close any PR, and post nothing else to it unless Tim asks; anything posted
starts with `🤖 *Posted by Claude via @timgladwell's account.*`.
No `kubectl`, `flux`, SSH or HTTP to a site — post-deploy checks are written
out for Tim to run.
