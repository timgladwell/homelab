#!/bin/bash
# Check that no ${VAR} reference survived step 3's substitution.
#
# Step 3 hydrates each site's built manifest with every ConfigMap that site's
# Kustomizations name in postBuild.substituteFrom, the way Flux does at reconcile
# time. So anything still looking like ${...} is a variable that site does not
# define — a typo, a missing entry in one of those ConfigMaps, or
# envsubst syntax the substitution pass does not implement (${VAR:=default}).
# Any of the three fails the whole Kustomization on the cluster: Flux v2.9
# substitutes in strict mode.
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

    # Resources that opt out of Flux substitution are dropped before scanning:
    # their ${...} tokens belong to the target application's own templating (a
    # Grafana dashboard's ${DS_PROMETHEUS}), not to Flux, and step 3 leaves them
    # alone for the same reason.
    leftover=$(awk 'BEGIN { RS = "\n---\n" }
        !/kustomize\.toolkit\.fluxcd\.io\/substitute: disabled/' "$BUILD_OUTPUT" |
        grep -oE '\$\{[^}]*\}' | sort -u)

    if [[ -n "$leftover" ]]; then
        echo "[$site] ERROR: unsubstituted variables left in the built output —"
        echo "        define them in clusters/${site}/cluster-vars.yaml (site-specific)"
        echo "        or clusters/common/network-vars.yaml (estate-wide):"
        echo "$leftover" | sed 's/^/  - /'
        fail=1
    else
        echo "[$site] ✓ fully substituted"
    fi
done
echo "CHECKED $checked sites"
exit $fail
