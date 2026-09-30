#!/bin/bash
# render-diff.sh — print, as Markdown, what a change does to every manifest as
# Flux will apply it, per site, for .github/workflows/render-diff.yml to post
# as a PR comment. CI-only, so it lives beside the workflow rather than in
# scripts/. Locally, read the same diff with flate's own human-readable output:
#
#   flate diff all -p clusters/akron --base origin/main
#
# A review aid, not a check. A diff cannot pass or fail — it needs reading — so
# it stays out of validate-k3s.sh, where every finding is an error. Validation
# step 15 is the pass/fail half of the same rendering.
#
# `flate diff all` covers both Kustomizations (after postBuild substitution)
# and chart-rendered objects. The chart half is the part nothing else shows:
# a Renovate chart bump is a one-line version change in the PR diff, and this
# is where its effect on the rendered StatefulSets and ConfigMaps appears.
#
# Both sides render at flate's bundled Kubernetes version, not the pinned k3s
# version step 15 uses. flate takes one --kube-version for both sides, so a k3s
# bump's effect on charts cannot be shown as a diff anyway, and step 15 already
# renders every chart at the new version.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
source scripts/validate/lib-sites.sh

base="${1:?usage: render-diff.sh <base-rev>}"
errors="$(mktemp)"
trap 'rm -f "$errors"' EXIT

echo "<!-- flate-render-diff -->"
echo "## Rendered manifest diff"
echo
echo "\`flate diff all\` against \`${base}\`: every manifest as Flux will apply it, including chart-rendered objects."
echo "A review aid; Validate's step 15 is the pass/fail check."

for site in $(sites); do
    diff_out="$(flate diff all -p "clusters/${site}" --base "$base" -o github --no-progress 2> "$errors")"
    rc=$?
    changes=$(grep -c '^@@ ' <<< "$diff_out")

    echo
    echo "### ${site}"
    if [[ $rc -ne 0 ]]; then
        cat "$errors" >&2
        # flate withholds a producer that failed to render on one side rather
        # than reporting its objects as wholesale adds or deletes, so a partial
        # diff here is still accurate for everything it shows.
        echo
        echo "flate exited ${rc}; anything that failed to render is left out of the diff below. Validate's step 15 reports why."
    fi
    echo
    if [[ -z "$diff_out" ]]; then
        echo "No rendered changes."
        continue
    fi
    # The diff is this PR's own content, rendered back into this PR, so it is
    # not reaching anyone the author could not already write to. The fence is
    # still longer than any backtick run the diff contains, so a manifest that
    # happens to hold a fence cannot end the code block early.
    longest=$(grep -o '`*' <<< "$diff_out" | awk '{ if (length > n) n = length } END { print n + 0 }')
    fence=$(printf '`%.0s' $(seq 1 $(( longest > 2 ? longest + 1 : 3 ))))
    echo "<details><summary>${changes} changed paths</summary>"
    echo
    echo "${fence}diff"
    echo "$diff_out"
    echo "${fence}"
    echo
    echo "</details>"
done
