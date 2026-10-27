# Contributing

Where each kind of information lives, and why. This applies to people and to
Claude Code alike; `CLAUDE.md` imports this file so it is loaded every session.

<!-- TEMPORARY(until: 2026-12-31, issue: #425): this taxonomy is trialled here
before being promoted to the global CLAUDE.md in the dotfiles repo. Where it
disagrees with the global file, this file wins. Retire by promoting it, then
replacing this section with whatever per-repo deltas remain. -->

## The rule

**Everything in source control describes current state.** History, plans,
findings and anything that *should* change live in issues. A file may point at
an issue; it never holds the work itself.

The one kind of document that describes something other than the deployed
system is an [ADR](#adrs): it specifies the **target** design. The gap between
the two is work, and lives in issues.

The one exception is something known to be temporary — a workaround, a
diagnostic metric, a runbook for a long-running investigation. It stays, but
carries a [`TEMPORARY` tag](#temporary-things) saying why and until when.

## Where things live

| Kind | Holds | Never holds |
|---|---|---|
| [Code comment](#code-comments) | Why this code is the way it is, and how to change it without breaking something | History, plans |
| [`CLAUDE.md`](#claudemd) | System-level boundaries and invariants, and an index of change-triggered runbooks | Anything a lower-level home can carry |
| [`docs/current-state/`](#current-state-docs) | The deployed system's state that code does not define | Procedures, plans, history |
| [`docs/adr/`](#adrs) | The target design that solves one product problem, and why it was chosen | How to implement it, progress toward it |
| [`docs/runbooks/`](#runbooks) | A procedure for a specific outcome | One-off history, past-run logs |
| [Issues](#issues) | Plans and their structure, tasks, findings, decisions, history | — |
| [Pull requests](#pull-requests) | Why this change is built this way | Analysis results, discovered work |
| [`README.md`](#readme) | Purpose, scope, getting started, licence | Everything else |
| Claude's memory | How the user prefers to work | Any technical fact |

Across all of them: describe what is true now, do not restate what `git log`
and `git blame` already answer, and never add a "TODO", "Next steps" or
"Action items" section — those are issues.

### Where a warning goes

A "don't change this without…" warning goes on the **narrowest thing that
exists**, because that is where it is read at the moment it matters:

1. **The line or block** it protects — a code comment.
2. **The module** — a header comment. In this repo a `kustomization.yaml` or
   HelmRelease header is module level.
3. **A runbook's pre-checks**, when the warning is about a *kind of change*
   rather than any existing line (renaming a Flux Kustomization, moving a
   resource between layers). `CLAUDE.md` indexes it in one line.
4. **A current-state doc**, for state that lives outside the code.

`CLAUDE.md` holds the warning itself only when it is genuinely system-wide.

## Code comments

A comment says why the code is the way it is and what else has to change with
it: "do not raise this without also raising the namespace quota in
`quota.yaml`; see #123 for how it was sized". The reasoning and history live
in the linked issue; the comment carries only what the next editor needs.

Comment length scales with the level of the code. A line gets one to a few
lines; a module or a component's `kustomization.yaml` may carry dozens. A long
comment on a single line usually belongs higher up.

## `CLAUDE.md`

Loaded into every session, so every line costs tokens on every task. It holds
only what applies at the system level:

- boundaries (what Claude must not touch) and repo-wide invariants;
- the architecture in outline, linking to ADRs and current-state docs for depth;
- an index of change-triggered runbooks: "before renaming a Flux Kustomization,
  follow [runbook]";
- `@CONTRIBUTING.md`.

Anything tied to specific code is a comment on that code instead. Incident
stories are issues.

## Current-state docs

`docs/current-state/` describes the parts of the deployed system that code
does not define: node configuration, UniFi and Cloudflare settings, the
bootstrap secrets, naming. Each says **what** the state is and how to change
it without breaking something. **How to reproduce it** — a fresh install, a
new site — is a provisioning runbook, and the two link to each other.

No change tables: the record of each change and its reasoning is in git
history, PRs and issues.

## ADRs

An ADR specifies the **target state** of part of the system: the design that
solves one product problem — "how does the system store observability data",
"how are secrets managed" — under the constraints it is solved in. It is a
system-design and decision-making document.

**It holds** the problem and its constraints, the decision, its consequences,
and the alternatives rejected, with the trade-offs that decided between them.
Implementation detail appears only where it is key to the decision: a technology
feature the choice depends on, or a limitation that rules an option out.

**It does not hold:**

- **How to carry out the implementation.** That is a runbook or an issue.
- **Progress toward the target.** It does not say what is built yet, link the
  issues delivering it, or describe how the deployed system is configured
  today. An issue cited as *evidence* for the decision (an incident, a
  measurement) belongs; an issue that *queues the work* to reach it does not.
  An ADR's status records the decision, not the implementation: `Accepted`
  means decided, not built.

**Targets and settings.** Concrete specifications (a pod's memory limit, a
chart value) are not ADR material: the value is in code, its derivation in an
issue. A **target** is different: a value whose change would change what the
system promises or accepts as lost, like how long an outage the collectors
survive. Choosing one is a trade-off, so the ADR states it, with its reasoning
and what it does not cover, and changing it is a revision of the ADR. The
settings that implement it are in code, each with a comment naming the ADR;
tuning a setting to meet a target stays there. The ADR names settings, not the
files they are in, because files move; the exception is an ADR whose decision
is the layout itself.

**It is revised in place, and expected to be.** It keeps its problem and is never
superseded by a new ADR; the answer changes when:

- **Implementing it teaches something.** An ADR is always at least a little
  off, and implementation is where the plan meets reality. A gap in the design
  or the target state goes back into the ADR. A lesson about the implementation
  stays in code and issues.
- **A constraint changes.** A design for ten users will not serve thousands,
  and a Raspberry Pi's limits stop applying once hosting moves to a proper
  server.

It is retired (`Deprecated`) only when its product problem no longer exists.

An ADR is the one document that keeps a change table: one short row per
evolution of the answer, so its history reads at a glance.

| Date | By | Description |
| --- | --- | --- |
| 2025-09-01 | Tim | Third-party API to send email during the bootstrap phase |
| 2026-09-01 | Tim and Claude | Self-hosted sendmail; scale now justifies running it in-house over third-party cost |

Format and numbering are in [`docs/adr/README.md`](docs/adr/README.md).

## Runbooks

A procedure for a specific outcome: **pre-checks** confirming scope and
origin, **steps**, and **post-checks** confirming the outcome. Each is one
file under `docs/runbooks/`, listed in [`docs/runbooks.md`](docs/runbooks.md)
with its kind:

| Kind | For | Examples |
|---|---|---|
| Operational | Running the system day to day, and responding when something happens | Promoting a release, resolving an alert, chasing slowness |
| Maintenance | Keeping the system healthy | Chart and host upgrades, sizing a pod's memory |
| Provisioning | Creating or recreating something | A new box, a new site |

A runbook may be temporary — written because a process will repeat a few times
during a long investigation. It carries a `TEMPORARY` tag at the top.

**Retiring a runbook** deletes the file and moves its row to the *Retired*
table in `docs/runbooks.md`, with a one-line summary and a link to the last
commit that contained it, so it can still be found.

## Issues

Everything that should change, and the record of how it got that way: plans,
tasks, findings, decisions, deferred work, and premises that turned out to be
wrong. Issues are cheap to close and expensive to miss.

**An issue must let someone start work from an empty context** — a person new
to it, or an agent with nothing else loaded. Capture enough that the research
does not need redoing: the evidence, the premises that turned out to be wrong,
and during an investigation the specific measurements, with when and how they
were taken.

A plan is a tree of GitHub sub-issues. The parent's body is the current plan,
edited in place as it changes; each child is a smaller area of the work, and
may have children of its own. Discussion goes in comments. A leaf is closed by
the PR that ships it, or by a decision recorded in it.

**An issue records what was understood when it was written**, not settled fact.
New information, new capabilities or more skill can overturn a conclusion. An
issue that does so names the premise the old conclusion rested on, and the
earlier issue gets a comment linking forward.

### Exploratory issues

Context worth keeping for work that is not scheduled, and may never be: a
brainstorm, a spike, a draft PR to look at. It stays open and labelled
`exploratory`, so the question of when stays visible. A backlog view leaves it
out with `-label:exploratory`.

The body is a parent issue like any other (the gap, the findings, premises that
turned out wrong, alternatives rejected), with two differences:

- **The planned work is a list in the body, not sub-issues**, so the backlog
  holds only work someone has committed to. A note at the top says so.
- **Every finding is dated**, because it will be read long after it was true.

Picking one up starts with rechecking its premises, its own findings included,
then creating the sub-issues. The label comes off and it becomes an ordinary
plan. One item can be split out earlier: open it as its own issue and replace
it in the list with the link.

Nothing in source control links an exploratory issue. It isn't planned, so it
says nothing about the current state.

### Observation issues

A record of a warning or error we can't yet interpret. Some we can judge on
sight, because we know the situation: requests dropped under high load, network
calls failing while the internet is down. For many others we lack the
situational information, technical knowledge or judgement to say what the error
*means*, or whether it is impactful enough to schedule work for. It might be an
expected transient during a restart, a normal part of running on this hardware,
or a real problem. Investigating every one is not a good use of effort, so when
an error isn't recognised and things appear to work, it is set aside for the
problems we know matter.

An observation issue keeps that from being lost. It captures data points over
time, so that either a pattern closes the gap directly, or, when the missing
knowledge arrives some other way, the data shows whether we are affected. It is
labelled `observation`. It is not a work item, and a backlog view leaves it out
with `-label:observation`.

- **The body says what to record and how to find an occurrence**: the metric,
  the log query, the fields each entry needs. It also keeps a short current
  state: what has been learned, what has been fixed, and what is still open.
- **Each occurrence is one comment**, dated, with every claim labelled
  *measured*, *hypothesis* or *unknown*. Occurrences where the thing *didn't*
  happen are worth recording too, because a pattern needs something to be
  compared against.
- **It turns into work over time.** Nothing is fixed from a single entry, but
  when entries point at a cause, a fix issue is opened and linked from here.
  One observation issue may spawn several fix issues, and gather their PRs,
  while it keeps collecting.
- **It closes when there is no further reason to capture observations**: a
  final fix has shipped, the watching has moved to observability tooling (an
  alert or a dashboard that records occurrences itself), or for some other
  reason given in the closing comment.

## Pull requests

The proposed change and nothing else: why it is built this way, the
alternatives rejected, and what a reviewer needs. When a finding drives the
change, the finding goes in an issue and the PR links it. Review replies answer
the review point.

## README

Three things: the repo's purpose and scope, how to start developing (a link is
fine), and the licence.

## Temporary things

Anything introduced as temporary carries a machine-readable tag where it lives:

```yaml
# TEMPORARY(until: 2026-12-01, issue: #123): <why it exists, what retires it>
```

In Markdown, the same inside `<!-- -->`. Both fields are required. The issue
owns the retirement work. Extending the date is a commit that changes it, with
the reason in the commit message.

## Dropped signals

[ADR 0003](docs/adr/0003-observability-collection-and-storage.md) collects
every metric and log by default and drops only for the hardware's sake, so a
drop has to be revisitable. Each one carries a tag where it is made:

```alloy
// DROP(saves: ~12k series, measured: 2026-10-01): <what is dropped, and why it is not needed>
```

Both fields are required. `saves` is the measured cost (series for metrics,
lines or bytes per day for logs) and `measured` is when it was measured.
Restoring a dropped signal for an investigation is a temporary change, tagged
as above.
