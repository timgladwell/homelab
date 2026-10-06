# 2. How changes roll out across sites

- **Status:** Accepted
- **Date:** 2026-08-06
- **Deciders:** Tim Gladwell

## Changelog

| Date | By | Description |
| --- | --- | --- |
| 2026-07-20 | Tim and Claude | Remote sites watch `stable`, promoted by PR |
| 2026-08-06 | Tim | Promotion by workflow fast-forward; `stable`'s ruleset reduced to rules that need no bypass (#206) |
| 2026-08-23 | Tim | `required_signatures` on both branches (#294) |
| 2026-10-06 | Tim and Claude | Written up as an ADR from CLAUDE.md and the retired reset runbook (#402) |

## Context

Every site reconciles from this one repository. A bad change that reaches every
site at once can take out DNS everywhere, including at remote sites where the
only way back in may depend on the DNS that just broke. Akron is local, so a
breakage there is recoverable by hand; the remote sites are not.

Constraints:

- The repository is **user-owned**, not in an organization. GitHub rejects app
  bypass actors (such as GitHub Actions) on rulesets for user-owned repos.
- Every commit that reaches a deployed branch must be signed.
- `main` allows only merge commits, so a PR's commits arrive on `main` verbatim.

## Decision

**Akron watches `main`; every other site watches `stable`.** Akron is the canary.

**`stable` is only ever fast-forwarded to a commit already on `main`**, by the
*Promote to stable* workflow (`.github/workflows/promote-to-stable.yml`), run by
hand. The workflow refuses to run unless the operator asserts Akron is healthy
and the commit has a successful `Validate` run. It never passes `--force`.
Procedure: [Promoting to `stable`](../runbooks/promote-to-stable.md).

**Rulesets, with no bypass actor on either branch:**

| Branch | Rules |
|---|---|
| `stable` | `deletion`, `non_fast_forward`, `required_signatures` |
| `main` | merge commits only, `required_signatures`, plus the PR rules |

With no bypass actor, `non_fast_forward` applies to everyone including the
repository owner, so `stable` can only move forward and nobody can rewrite it.

## Consequences

**Positive**

- `stable` and `main` share SHAs and cannot diverge; git always knows `stable`
  is an ancestor of `main`.
- The protection on `stable` is stronger than a bypass would allow: no actor,
  including the owner, can force-push or delete it.

**Negative and risks**

- Promotion is manual. There is no automatic cross-site health gate; the gate is
  the operator's assertion.
- **An unsigned commit on `main` cannot be repaired.** `main`'s history is
  immutable too, and every later promotion carries the commit to `stable`, where
  `required_signatures` rejects the push until `stable`'s ruleset is temporarily
  disabled (#294).
- A fast-forward push to `stable` by hand is not blocked. It is caught only by
  the next promotion failing its fast-forward check. `flux bootstrap` with a
  mismatched CLI version is the realistic way this happens; the
  [bootstrap runbook](../runbooks/bootstrap-new-remote-site.md) guards it.

## Alternatives considered

- **Promote by PR into `stable`.** Used until 2026-08-06. `stable` allowed only
  rebase merges, which replay commits under new SHAs, so every promotion left
  content-identical commits that git could not match. The merge base fell behind
  on each promotion until a large PR's conflicts became unresolvable (#206).
  `stable` also required linear history, which a `main` with merge commits can
  never satisfy, so the two rulesets were incompatible by construction.
- **Let the workflow bypass the ruleset (GitHub Actions app).** Impossible on a
  user-owned repo: `Actor GitHub Actions integration must be part of the ruleset
  source or owner organization`.
- **A repository-scoped PAT with a `RepositoryRole` bypass.** Works, but
  `bypass_mode: always` bypasses every rule, so the owner could then force-push or
  delete `stable`. It also adds a credential to rotate. Worse than no bypass.
- **Squash or rebase merges on `main`.** Would replace a PR's commits with a
  GitHub-signed commit, hiding an unsigned commit until promotion instead of
  rejecting it at merge.
