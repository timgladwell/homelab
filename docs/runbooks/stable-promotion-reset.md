# One-Time Reset of `stable`, and Switching to Fast-Forward Promotion

Run once, to move promotion from "open a PR into `stable`" to "fast-forward
`stable` to `main`". After this, `stable` can never diverge from `main` again.

## Why

`main`'s ruleset allowed only **merge** commits. `stable`'s allowed only
**rebase**. Rebase-and-merge replays commits under new SHAs, so every promotion
left `stable` holding commits that were byte-identical in content to `main`'s
but unrelated in ancestry. Git could not tell they were the same work, so the
merge base fell further behind on each promotion and conflicts accumulated.

`stable` also had `required_linear_history`, while `main` has merge commits — so
`stable` could never fast-forward from `main` under its own rules. The two
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

## Why there is no bypass actor

The obvious design is to let the promotion workflow bypass the ruleset. On a
**personal** repository you cannot: adding the GitHub Actions app as a bypass
actor is rejected.

```
Actor GitHub Actions integration must be part of the ruleset source
or owner organization
```

App bypass actors require an organization. This is the same constraint the
[2022 changelog on apps as branch-protection exceptions](https://github.blog/changelog/2022-05-17-consistently-allow-github-apps-as-exceptions-to-branch-protection-rules/)
describes; rulesets did not lift it for user-owned repos.

A repository-scoped PAT plus a `RepositoryRole` bypass would work, but it is
**worse than having no bypass at all**. `bypass_mode: always` bypasses *every*
rule, so the bypassing actor — you, as admin — could then force-push or delete
`stable`. It also adds a credential to rotate.

Instead, shape the ruleset so no bypass is needed. `stable` is no longer a
branch anyone merges into; it is a pointer that only ever moves forward to a
commit already on `main`. The rules that matter are the ones preventing
*history* changes, and those should apply to everyone, including you:

| Rule | Keep? | Why |
|---|---|---|
| `deletion` | **keep** | `stable` must never be deleted |
| `non_fast_forward` | **keep** | the real protection — no force-push, no history rewrite, by anyone |
| `pull_request` | **remove** | nothing is merged into `stable` any more, and it is what blocks the workflow's push |
| `required_linear_history` | **remove** | permanently unsatisfiable against a `main` that carries merge commits |

The result is stronger than a bypass would have been: with `non_fast_forward`
enforced and no bypass actor, `stable` can only ever move forward, and not even
the repository owner can rewrite it. The only remaining accident is pushing a
fast-forward commit to `stable` by hand — which the next promotion catches
loudly, because the fast-forward check will fail.

## Steps

1. **Check current enforcement.** It may have been disabled during earlier
   experimenting, in which case `stable` has no protection at all right now:

   ```bash
   RULESET=$(gh api repos/timgladwell/homelab/rulesets \
     --jq '.[] | select(.name|test("stable")) | .id')
   gh api "repos/timgladwell/homelab/rulesets/$RULESET" --jq '.enforcement'
   ```

2. **Close the promotion PR** if one is open. It cannot produce the right
   result — rebase-merging it would replay main's commits onto `stable` under
   fresh SHAs again, recreating the divergence it is meant to fix.

3. **Reduce the ruleset to the two history rules.** Read-modify-write, so
   nothing else about the ruleset changes:

   ```bash
   gh api "repos/timgladwell/homelab/rulesets/$RULESET" | jq '{
     name, target, enforcement, conditions, bypass_actors,
     rules: [.rules[] | select(.type == "deletion" or .type == "non_fast_forward")]
   }' | gh api --method PUT "repos/timgladwell/homelab/rulesets/$RULESET" --input -

   gh api "repos/timgladwell/homelab/rulesets/$RULESET" \
     --jq '{enforcement, rules: [.rules[].type], bypass_actors}'
   ```

   Expect `["deletion","non_fast_forward"]`, `enforcement: active`, and an empty
   `bypass_actors`.

4. **Reset `stable` to `main`.** A fast-forward is impossible until this is
   done, and this is the only force-push the scheme will ever need.
   `non_fast_forward` blocks it, so flip enforcement off and straight back:

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

5. **Verify a fast-forward is now possible:**

   ```bash
   git fetch origin
   git merge-base --is-ancestor origin/stable origin/main && echo "fast-forward OK"
   ```

## From then on

Promote from the Actions tab: **Promote to stable** → Run workflow → set
`akron_healthy` to `yes`.

The workflow refuses to run if Akron has not been confirmed healthy, if the
commit being promoted has no successful `Validate` run, or if the move would not
be a fast-forward. It never passes `--force`, so git refuses a non-fast-forward
push regardless, and the ruleset refuses it a second time.

## If a future promotion reports "not a fast-forward"

Something wrote to `stable` outside the workflow. Do not force anything. Re-run
the `git cherry` check at the top of this page to find out whether the extra
commits are genuinely unique. If they are, get them onto `main` first; if they
are duplicates, repeat step 4.
