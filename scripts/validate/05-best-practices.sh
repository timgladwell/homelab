#!/bin/bash
# Check Kubernetes best practices against each site's built manifest.
# Warnings are errors here too — use kube-score's --ignore-test flags in this
# script if a specific check doesn't apply, not a passing grade threshold.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
source "$(dirname "$0")/lib-sites.sh"
BUILD_DIR="${K3S_BUILD_DIR:-${TMPDIR:-/tmp}}"

fail=0
for site in $(sites); do
    BUILD_OUTPUT="${BUILD_DIR}/k3s-built-${site}.yaml"
    if [[ ! -f "$BUILD_OUTPUT" ]]; then
        echo "ERROR: $BUILD_OUTPUT not found — run 03-kustomize-build.sh first" >&2
        exit 1
    fi

    # JSON + jq instead of the human table: a passing run has hundreds of
    # per-object/per-check [OK] lines that add nothing once every one of
    # them is OK. Only checks that didn't pass are worth printing.
    # --output-version pins the JSON shape so a kube-score upgrade can't
    # silently change field names out from under the jq query below.
    json=$(kube-score score -o json --output-version v2 "$BUILD_OUTPUT" 2>&1) || {
        echo "--- $site ---"
        echo "$json"
        fail=1
        continue
    }

    findings=$(jq -r '
        .[] | .object_name as $o |
        .checks[] | select(.skipped == false and .grade < 10) |
        "\($o): \(.check.id) (grade \(.grade)) " +
        ([.comments[]?.summary] | join("; "))
    ' <<<"$json") || {
        # A jq error here almost always means kube-score's JSON shape
        # changed — fail loudly instead of silently reporting no findings.
        echo "--- $site ---"
        echo "ERROR: failed to parse kube-score JSON output — schema may have changed"
        fail=1
        continue
    }
    if [[ -n "$findings" ]]; then
        echo "--- $site ---"
        echo "$findings"
        fail=1
    fi
done
exit $fail
