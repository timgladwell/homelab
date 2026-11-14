#!/bin/bash
# Run conftest against each site's built manifest using policies in policy/.
# Depends on the kustomize build output from 03-kustomize-build.sh.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="${K3S_BUILD_DIR:-${TMPDIR:-/tmp}}"
cd "$REPO_ROOT"
source "$(dirname "$0")/lib-sites.sh"

fail=0
checked=0
for site in $(sites); do
    BUILD_OUTPUT="${BUILD_DIR}/k3s-built-${site}.yaml"
    if [[ ! -f "$BUILD_OUTPUT" ]]; then
        echo "ERROR: $BUILD_OUTPUT not found — run 03-kustomize-build.sh first" >&2
        exit 1
    fi
    checked=$((checked + 1))
    echo "--- $site ---"
    conftest test "$BUILD_OUTPUT" \
        --policy "$REPO_ROOT/policy" \
        --all-namespaces \
        --fail-on-warn \
        --no-color || fail=1
done
echo "CHECKED $checked sites"
exit $fail
