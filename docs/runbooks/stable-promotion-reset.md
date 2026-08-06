# One-Time Reset of `stable`, and Switching to Fast-Forward Promotion

Run once, to move promotion from "open a PR into `stable`" to "fast-forward
`stable` to `main`". After this, `stable` can never diverge from `main` again.

## Why

`main`'s ruleset allowed only **merge** commits. `stable`'s allowed only
**rebase**. Rebase-and-merge replays commits under new SHAs, so every promotion
left `stable` holding commits that were byte-identical in content to `main`'s
but unrelated in ancestry. Git could not tell they were the same work, so the
merge base fell further behind on each promotion and conflicts accumulated.

`stable` also had `required_linear_history`, while `main` has 53 merge commits —
so `stable` could never fast-forward from `main` under its own rules. The two
rulesets were mutually incompatible by construction.

Confirm the divergence is cosmetic before resetting anything:

```bash
git fetch origin
git cherry origin/main origin/stable
```

A `-` prefix on every line means every commit unique to `stable` is already in
`main` under a different SHA — nothing would be lost. If any line has a `+`,
**stop**: that commit is genuinely unique to `stable` and must be brought into
`main` first.

Double-check by comparing the trees directly:

```bash
git diff --stat origin/stable origin/main
```

That should show only the changes you are about to promote.

## Steps

1. **Close the promotion PR** if one is open. It cannot produce the right
   result — rebase-merging it would replay main's commits onto `stable` under
   fresh SHAs again, recreating the divergence it is meant to fix.

2. **Add the GitHub Actions app as a bypass actor on the `stable` ruleset.**
   This is what lets the workflow push while everyone else stays blocked.

   ```bash
   RULESET=$(gh api repos/timgladwell/homelab/rulesets \
     --jq '.[] | select(.name|test("stable")) | .id')

   gh api --method PUT "repos/timgladwell/homelab/rulesets/$RULESET" \
     -f 'bypass_actors[][actor_id]=15368' \
     -f 'bypass_actors[][actor_type]=Integration' \
     -f 'bypass_actors[][bypass_mode]=always'
   ```

   `15368` is the GitHub Actions app (`gh api /apps/github-actions --jq .id`).
   Verify it took:

   ```bash
   gh api "repos/timgladwell/homelab/rulesets/$RULESET" --jq '.bypass_actors'
   ```

3. **Reset `stable` to `main`.** This is the only force-push in the procedure,
   and the only one that will ever be needed:

   ```bash
   git push --force origin origin/main:refs/heads/stable
   ```

   The `non_fast_forward` rule blocks this for you as a human. Either run it
   from the Actions bypass (temporarily set `enforcement` to `disabled`, push,
   set it back to `active`), or do the reset in the GitHub UI. Confirm the
   ruleset is `active` again afterwards:

   ```bash
   gh api "repos/timgladwell/homelab/rulesets/$RULESET" --jq '.enforcement'
   ```

4. **Verify a fast-forward is now possible:**

   ```bash
   git fetch origin
   git merge-base --is-ancestor origin/stable origin/main && echo "fast-forward OK"
   ```

5. **Tidy the now-meaningless rules on `stable`** (optional but recommended).
   `required_linear_history` and `allowed_merge_methods: ["rebase"]` describe a
   promote-by-PR model that no longer exists, and the linear-history rule is
   permanently unsatisfiable against a `main` that carries merge commits.
   Keeping them is misleading. Keep `deletion`, `non_fast_forward` and
   `pull_request` — together with the bypass, those mean "only the promotion
   workflow may move `stable`".

## From then on

Promote from the Actions tab: **Promote to stable** → Run workflow → set
`akron_healthy` to `yes`.

The workflow refuses to run if Akron has not been confirmed healthy, if the
commit being promoted has no successful `Validate` run, or if the move would
not be a fast-forward. It never passes `--force`, so it cannot rewrite
`stable`'s history even with the bypass — git refuses a non-fast-forward push
on its own.

## If a future promotion reports "not a fast-forward"

Something wrote to `stable` outside the workflow. Do not force anything.
Re-run the `git cherry` check at the top of this page to find out whether the
extra commits are genuinely unique. If they are, get them onto `main` first;
if they are duplicates, repeat step 3.
