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

## Before you start

Check whether the ruleset is currently enforced — it may have been disabled
during earlier experimenting, in which case `stable` has no protection at all
right now:

```bash
RULESET=$(gh api repos/timgladwell/homelab/rulesets \
  --jq '.[] | select(.name|test("stable")) | .id')
gh api "repos/timgladwell/homelab/rulesets/$RULESET" --jq '.enforcement'
```

## Steps

1. **Close the promotion PR** if one is open. It cannot produce the right
   result — rebase-merging it would replay main's commits onto `stable` under
   fresh SHAs again, recreating the divergence it is meant to fix.

2. **Add the GitHub Actions app as a bypass actor on the `stable` ruleset.**
   This is what lets the workflow push while everyone else stays blocked.

   `actor_id` is an **integer**, so `gh api -f` will not work — it sends every
   value as a string and the API rejects it with
   `Invalid property /bypass_actors/0/actor_id: "15368" is not of type integer`.
   Send a JSON body instead. Read-modify-write, so no existing rule is dropped:

   ```bash
   RULESET=$(gh api repos/timgladwell/homelab/rulesets \
     --jq '.[] | select(.name|test("stable")) | .id')

   gh api "repos/timgladwell/homelab/rulesets/$RULESET" | jq '{
     name, target, enforcement, conditions, rules,
     bypass_actors: (.bypass_actors + [{
       actor_id: 15368, actor_type: "Integration", bypass_mode: "always"
     }])
   }' | gh api --method PUT "repos/timgladwell/homelab/rulesets/$RULESET" --input -
   ```

   `15368` is the GitHub Actions app ID (`gh api /apps/github-actions --jq .id`),
   not an installation ID. Verify it took:

   ```bash
   gh api "repos/timgladwell/homelab/rulesets/$RULESET" --jq '.bypass_actors'
   ```

   Rulesets are **not** classic branch protection. The [2022 changelog about
   apps as branch-protection exceptions](https://github.blog/changelog/2022-05-17-consistently-allow-github-apps-as-exceptions-to-branch-protection-rules/)
   describes the older system, where the app had to be installed with write
   access. Rulesets take `Integration` bypass actors directly on a repository.

   **If GitHub rejects the `Integration` actor**, fall back to a token that acts
   as a user holding a bypass role:

   - Create a fine-grained PAT scoped to this repository only, with
     `Contents: Read and write`, stored as the repository secret
     `PROMOTE_TOKEN`.
   - In `promote-to-stable.yml`, pass it to the checkout so the push uses it:
     `token: ${{ secrets.PROMOTE_TOKEN }}`.
   - Add a `RepositoryRole` bypass actor instead (`actor_id: 5` is admin).

   Prefer the app bypass. The role bypass is weaker: it also lets *you* push to
   `stable` by hand, which is the accident this is meant to prevent. It also
   adds a credential to rotate.

3. **Reset `stable` to `main`.** This is the only force-push in the procedure,
   and the only one that will ever be needed:

   ```bash
   git push --force origin origin/main:refs/heads/stable
   ```

   The `non_fast_forward` rule blocks this for you as a human, so flip
   enforcement off, push, and flip it straight back:

   ```bash
   set_enforcement() {
     gh api "repos/timgladwell/homelab/rulesets/$RULESET" \
       | jq --arg e "$1" '{name, target, enforcement: $e, conditions, rules, bypass_actors}' \
       | gh api --method PUT "repos/timgladwell/homelab/rulesets/$RULESET" --input -
   }

   set_enforcement disabled
   git push --force origin origin/main:refs/heads/stable
   set_enforcement active
   ```

   **Confirm enforcement is back on.** Leaving it disabled silently removes all
   protection from `stable` and nothing will remind you:

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
