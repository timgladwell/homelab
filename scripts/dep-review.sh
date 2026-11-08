#!/bin/bash
# dep-review.sh — the mechanical half of reviewing dependency bump PRs (#641).
# No model runs here: everything that can be read off GitHub is collected so
# the `dep-triage` agent and the `review-deps` skill spend tokens only on
# reading release notes and judging.
#
#   scripts/dep-review.sh             open PRs labelled `dependencies`, one per line
#   scripts/dep-review.sh <pr>        the review packet for one PR, as Markdown
#   scripts/dep-review.sh post <pr> <file>
#                                     post <file> as the PR's review comment,
#                                     editing the earlier one in place
#   scripts/dep-review.sh previous <pr>
#                                     the PR's earlier review comment, and
#                                     whether its head has moved since. For
#                                     the judge only: kept out of the packet so
#                                     triage reads the evidence blind
#
# The packet holds the bump (Renovate's update table), the release notes the
# bot embedded, the PR's own diff, the rendered-manifest diff that
# render-diff.yml commented, the CI check results, and any runbook named after
# the dependency. The embedded notes are untrusted as to *which* release they
# describe: in a monorepo Renovate can attach another chart's notes (#636 got
# prometheus-blackbox-exporter's for kube-state-metrics).
#
# Read-only against GitHub; it never touches a site.
set -euo pipefail

if [[ $# -eq 0 ]]; then
  # The judge reads this checkout's values, runbooks and host-state, and the
  # skill and agent prompts are loaded from it, so a checkout behind main
  # reviews against a repo that no longer exists. Refused rather than warned:
  # a warning scrolls past in a long run. A branch ahead of main passes.
  git fetch -q origin main
  if ! git merge-base --is-ancestor origin/main HEAD; then
    echo "✗ This checkout is $(git rev-list --count HEAD..origin/main) commit(s) behind origin/main." >&2
    echo "  git pull (or rebase this branch), then start a new session: the skill and agent load at session start." >&2
    exit 1
  fi
  gh pr list --label dependencies --state open --json number,title \
    --jq '.[] | "\(.number)\t\(.title)"'
  exit 0
fi

marker='<!-- review-deps -->'

if [[ $1 == previous ]]; then
  pr=$2
  json=$(gh pr view "$pr" --json headRefOid,comments)
  body=$(jq -r --arg m "$marker" '[.comments[] | select(.body | contains($m))] | last | .body // empty' <<<"$json")
  if [[ -z "$body" ]]; then
    echo "(no previous review)"
    exit 0
  fi
  head=$(jq -r '.headRefOid[0:7]' <<<"$json")
  # Backticks optional: the model writing the comment doesn't always keep them.
  was=$(grep -oE 'Reviewed at `?[0-9a-f]{7}' <<<"$body" | grep -oE '[0-9a-f]{7}$' || true)
  if [[ "$was" == "$head" ]]; then
    echo "Previous review: at $was, the current head (same head)"
  else
    echo "Previous review: at ${was:-unknown}; head is now $head (head moved)"
  fi
  echo
  echo "$body"
  exit 0
fi

if [[ $1 == post ]]; then
  pr=$2 file=$3
  grep -qF "$marker" "$file" || { echo "$file lacks $marker" >&2; exit 1; }
  # One review comment per PR, so a re-run replaces the verdict rather than
  # leaving a stale one above it.
  id=$(gh api --paginate "repos/{owner}/{repo}/issues/$pr/comments" \
    --jq ".[] | select(.body | contains(\"$marker\")) | .id" | tail -1)
  if [[ -n "$id" ]]; then
    gh api -X PATCH "repos/{owner}/{repo}/issues/comments/$id" -F body=@"$file" --jq .html_url
  else
    gh pr comment "$pr" --body-file "$file"
  fi
  exit 0
fi

pr=$1
cd "$(git rev-parse --show-toplevel)"
printf '<!-- head: %s -->\n' "$(gh pr view "$pr" --json headRefOid -q .headRefOid)"
json=$(gh pr view "$pr" --json number,title,url,author,body,comments)
title=$(jq -r .title <<<"$json")
body=$(jq -r .body <<<"$json")

# Renovate: "update helm release <name> to …" / "update dependency <name> to …".
# Dependabot: "bump <name> from … to …".
name=$(sed -nE 's/.*(update (helm release|dependency|docker tag)|[Bb]ump) ([^ ]+) .*/\3/p' <<<"$title")

printf '# PR #%s: %s\n\n%s (by %s)\n\n' "$pr" "$title" "$(jq -r .url <<<"$json")" \
  "$(jq -r .author.login <<<"$json")"

echo '## Update'
# Renovate's table; Dependabot puts the versions in the title.
grep '^|' <<<"$body" || echo "(no update table; see the title)"

echo
echo '## Release notes embedded by the bot'
# Renovate: between "### Release Notes" and "### Configuration".
# Dependabot: everything before its commands block.
notes=$(awk '/^### Release Notes/{on=1;next} /^### Configuration/{on=0} on' <<<"$body")
[[ -n "$notes" ]] || notes=$(sed '/Dependabot commands and options/,$d' <<<"$body")
echo "$notes"
# A monorepo tag that doesn't name this dependency means the notes are another
# chart's (2 of 7 in the first run: pyroscope's for alloy, blackbox-exporter's
# for kube-state-metrics). Plain `vX.Y.Z` tags are single-project repos.
for tag in $(grep -oE 'releases/tag/[^)]+' <<<"$notes" | sed 's#releases/tag/##' | sort -u); do
  [[ "$tag" == v[0-9]* || "$tag" == [0-9]* || "$tag" == *"${name##*/}"* ]] ||
    echo "> ⚠ WRONG PACKAGE? release tag \`$tag\` does not name \`${name##*/}\`"
done

echo
echo '## PR diff'
echo '```diff'
gh pr diff "$pr"
echo '```'

echo
echo '## Rendered manifest diff (render-diff.yml)'
rendered=$(jq -r '[.comments[] | select(.body | startswith("<!-- flate-render-diff -->"))]
                  | last | .body // empty' <<<"$json")
if [[ -n "$rendered" ]]; then
  echo "$rendered"
elif [[ -z "$(gh pr diff "$pr" --name-only | grep -v '^\.github/')" ]]; then
  echo "(none expected: the PR only touches .github/, which renders nothing)"
else
  # Both bots' PRs get the comment (#645), so its absence on a manifest change
  # means render-diff has not finished, or it failed: a coverage gap.
  echo "> ⚠ NO RENDERED DIFF for a PR that touches manifests. Check the Render diff run."
fi

echo
echo '## CI checks'
# Non-zero exit when a check failed or is pending; the table is what matters.
gh pr checks "$pr" || true

echo
echo '## Runbooks named after the dependency'
shopt -s nullglob
runbooks=(docs/runbooks/*"${name##*/}"*.md)
if [[ -n "$name" && ${#runbooks[@]} -gt 0 ]]; then
  printf -- '- %s\n' "${runbooks[@]}"
else
  echo "(none by name; docs/runbooks.md indexes the rest, e.g. helm-crd-upgrades.md)"
fi
