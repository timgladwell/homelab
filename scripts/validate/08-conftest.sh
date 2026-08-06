#!/bin/bash
# Run conftest against each site's built manifest using policies in policy/.
# Depends on the kustomize build output from 03-kustomize-build.sh.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="${K3S_BUILD_DIR:-${TMPDIR:-/tmp}}"
cd "$REPO_ROOT"

fail=0
for site in $(ls -d clusters/*-validation | sed 's|clusters/||; s|-validation||'); do
    BUILD_OUTPUT="${BUILD_DIR}/k3s-built-${site}.yaml"
    if [[ ! -f "$BUILD_OUTPUT" ]]; then
        echo "ERROR: $BUILD_OUTPUT not found — run 03-kustomize-build.sh first" >&2
        exit 1
    fi
    echo "--- $site ---"
    conftest test "$BUILD_OUTPUT" \
        --policy "$REPO_ROOT/policy" \
        --all-namespaces \
        --no-color || fail=1
done
exit $fail
