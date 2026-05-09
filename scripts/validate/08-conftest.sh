#!/bin/bash
# Run conftest against the built manifest using policies in policy/.
# Depends on the kustomize build output from 02-kustomize-build.sh.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_OUTPUT="${K3S_BUILD_OUTPUT:-${TMPDIR:-/tmp}/k3s-built.yaml}"

if [[ ! -f "$BUILD_OUTPUT" ]]; then
    echo "ERROR: $BUILD_OUTPUT not found — run 02-kustomize-build.sh first" >&2
    exit 1
fi

conftest test "$BUILD_OUTPUT" \
    --policy "$REPO_ROOT/policy" \
    --all-namespaces \
    --no-color
