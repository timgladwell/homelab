#!/bin/bash
# Check Kubernetes best practices against each site's built manifest.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
BUILD_DIR="${K3S_BUILD_DIR:-${TMPDIR:-/tmp}}"

fail=0
for site in $(ls -d clusters/*-validation | sed 's|clusters/||; s|-validation||'); do
    BUILD_OUTPUT="${BUILD_DIR}/k3s-built-${site}.yaml"
    if [[ ! -f "$BUILD_OUTPUT" ]]; then
        echo "ERROR: $BUILD_OUTPUT not found — run 03-kustomize-build.sh first" >&2
        exit 1
    fi
    echo "--- $site ---"
    kube-score score "$BUILD_OUTPUT" || fail=1
done
exit $fail
