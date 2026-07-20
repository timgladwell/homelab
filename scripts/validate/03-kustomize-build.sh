#!/bin/bash
# Build each site's kustomization and write output to $K3S_BUILD_DIR/k3s-built-<site>.yaml.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
BUILD_DIR="${K3S_BUILD_DIR:-${TMPDIR:-/tmp}}"

fail=0
for site in akron eastbank lottage; do
    if ! kustomize build "./clusters/${site}-validation" > "${BUILD_DIR}/k3s-built-${site}.yaml"; then
        echo "✗ [$site] kustomize build failed"
        fail=1
    fi
done
exit $fail
