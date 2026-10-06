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
   too and refuses without one.

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
