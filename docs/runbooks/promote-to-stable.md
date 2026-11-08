# Promoting to `stable`

Moves the remote sites to a revision Akron has already proven, by
fast-forwarding `stable` to a commit on `main`. Why it works this way, and the
rulesets that make it safe: [ADR 0002](../adr/0002-rollout-across-sites.md).

## Pre-checks

1. **Akron has reconciled the commit you are promoting, and is healthy.** On
   Akron, every Kustomization is `Ready` at that revision:

   ```bash
   flux get kustomizations -A
   ```

2. **The commit has a green `Validate` run on `main`.** The workflow checks this
   too and refuses without one. A run that failed on something transient (a
   download 5xx) is re-run, not overridden: `gh run rerun --failed <run-id>`.
   If CI itself is broken and a fix has to reach the remote sites anyway, see
   [Promoting while CI is broken](#promoting-while-ci-is-broken).

## Steps

From the Actions tab: **Promote to stable** → Run workflow → set
`akron_healthy` to `yes`. Or:

```bash
gh workflow run promote-to-stable.yml -f akron_healthy=yes
```

The workflow refuses to run without the health assertion, without a green
`Validate` run, or if the move is not a fast-forward. It never passes
`--force`.

## Post-checks

On each remote site, the source is at the promoted revision and every
Kustomization is `Ready`:

```bash
flux get sources git
flux get kustomizations -A
```

## Promoting while CI is broken

Break-glass, for when `Validate` can't go green for reasons that have nothing
to do with the commit (an outage, a broken CI dependency) and the remote sites
need a fix now. It lifts only the `Validate` gate: Akron must still be
confirmed healthy, and the push must still fast-forward.

Before using it, check the failure yourself: read the failed run and be sure
it's CI that's broken, not the tree. Then give the reason as the override:

```bash
gh workflow run promote-to-stable.yml -f akron_healthy=yes \
  -f validate_override="<why CI can't pass, and the run you checked>"
```

The run logs a warning and records the reason and the Validate conclusion in
its summary. Leaving the field empty keeps the normal gate.

**Post-checks**, beyond the usual ones: once CI is fixed, confirm `Validate`
passes on a `main` commit at or after the one you promoted, so the tree on
`stable` has been validated after all.

## If the push is rejected

### "Not a fast-forward"

Something wrote to `stable` outside the workflow. **Do not force anything yet.**
Find out whether the extra commits are genuinely unique:

```bash
git fetch origin
git cherry origin/main origin/stable
```

- **Any line starting `+`**: that commit exists only on `stable`. Get it onto
  `main` through a PR first, then promote again.
- **Every line starting `-`**: the commits are already on `main` under different
  SHAs, and nothing would be lost. Confirm with
  `git diff --stat origin/stable origin/main`, which should show only what you
  are promoting, then [reset `stable` to `main`](#resetting-stable-to-main).

### Signature rejected

An unsigned commit reached `main`. It cannot be removed from `main`, so `stable`
can only accept it with its ruleset briefly disabled — follow
[Resetting `stable` to `main`](#resetting-stable-to-main), which pushes
`main` as-is. Separately, find how the unsigned commit got past `main`'s
ruleset (#294).

## Resetting `stable` to `main`

The only force-push this scheme ever needs. `non_fast_forward` and
`required_signatures` block it, so enforcement is switched off and straight
back on.

```bash
RULESET=$(gh api repos/timgladwell/homelab/rulesets \
  --jq '.[] | select(.name|test("stable")) | .id')

set_enforcement() {
  gh api "repos/timgladwell/homelab/rulesets/$RULESET" \
    | jq --arg e "$1" '{name, target, enforcement: $e, conditions, rules, bypass_actors}' \
    | gh api --method PUT "repos/timgladwell/homelab/rulesets/$RULESET" --input -
}

set_enforcement disabled
git push --force origin origin/main:refs/heads/stable
set_enforcement active
```

`set_enforcement` reads the ruleset and writes it back with only `enforcement`
changed. Keep it that way: an earlier version listed the rules to keep by hand
and silently dropped `required_signatures` when that rule was added.

**Post-checks — both are required.** Leaving enforcement disabled silently
removes all protection from `stable`, and nothing will remind you:

```bash
gh api "repos/timgladwell/homelab/rulesets/$RULESET" \
  --jq '{enforcement, rules: [.rules[].type], bypass_actors}'
# expect: enforcement "active"; rules deletion, non_fast_forward,
# required_signatures in any order; bypass_actors empty

git fetch origin
git merge-base --is-ancestor origin/stable origin/main && echo "fast-forward OK"
```
