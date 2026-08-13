#!/bin/bash
# Check Kubernetes best practices against each site's built manifest.
# Warnings are errors here too — use kube-score's --ignore-test flags in this
# script if a specific check doesn't apply, not a passing grade threshold.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
source "$(dirname "$0")/lib-sites.sh"
BUILD_DIR="${K3S_BUILD_DIR:-${TMPDIR:-/tmp}}"

fail=0
checked=0
for site in $(sites); do
    BUILD_OUTPUT="${BUILD_DIR}/k3s-built-${site}.yaml"
    if [[ ! -f "$BUILD_OUTPUT" ]]; then
        echo "ERROR: $BUILD_OUTPUT not found — run 03-kustomize-build.sh first" >&2
        exit 1
    fi
    checked=$((checked + 1))

    # kube-score's own `ci` format, which is already one line per check:
    #   [CRITICAL] pihole/dns apps/v1/Deployment: (pihole) CPU limit is not set
    # Dropping the [OK] and [SKIPPED] lines leaves exactly the findings, with
    # severity, object and container intact — no parsing, so nothing here can
    # break when kube-score's internals change.
    #
    # Deliberately not the JSON or SARIF output. SARIF looks like the portable
    # choice and is not: kube-score's SARIF drops the object name entirely and
    # points every finding at line 1 of the file, which for a concatenated
    # per-site build means every finding has the same useless location.
    #
    # --exit-one-on-warning is what makes warnings errors. Use --ignore-test
    # here if a specific check doesn't apply; never a grade threshold.
    output=$(kube-score score --exit-one-on-warning -o ci "$BUILD_OUTPUT" 2>&1)
    rc=$?
    findings=$(grep -vE '^\[(OK|SKIPPED)\]' <<< "$output")
    if [[ $rc -ne 0 || -n "$findings" ]]; then
        echo "--- $site ---"
        echo "$findings"
        fail=1
    fi
done
echo "CHECKED $checked sites"
exit $fail
