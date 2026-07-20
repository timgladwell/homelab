#!/bin/bash
# Validate Kubernetes schemas against each site's built manifest.
BUILD_DIR="${K3S_BUILD_DIR:-${TMPDIR:-/tmp}}"

# Known false positives — kubeconform has no bundled schemas for these:
#   CustomResourceDefinition: the Flux CRD definition objects themselves
#   Kustomization, GitRepository: Flux CR instances (toolkit.fluxcd.io CRDs)
# These are skipped explicitly so the summary reflects them as "Skipped" not "Errors".
SKIP_KINDS="CustomResourceDefinition,Kustomization,GitRepository"

fail=0
for site in akron eastbank lottage; do
    BUILD_OUTPUT="${BUILD_DIR}/k3s-built-${site}.yaml"
    if [[ ! -f "$BUILD_OUTPUT" ]]; then
        echo "ERROR: $BUILD_OUTPUT not found — run 03-kustomize-build.sh first" >&2
        exit 1
    fi
    echo "--- $site ---"
    kubeconform \
        -skip "$SKIP_KINDS" \
        -ignore-missing-schemas \
        -summary \
        "$BUILD_OUTPUT" || fail=1
done
exit $fail
