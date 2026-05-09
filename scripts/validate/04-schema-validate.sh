#!/bin/bash
# Validate Kubernetes schemas against /tmp/k3s-built.yaml.
BUILD_OUTPUT="${K3S_BUILD_OUTPUT:-${TMPDIR:-/tmp}/k3s-built.yaml}"
if [[ ! -f "$BUILD_OUTPUT" ]]; then
    echo "ERROR: $BUILD_OUTPUT not found — run 02-kustomize-build.sh first" >&2
    exit 1
fi

# Known false positives — kubeconform has no bundled schemas for these:
#   CustomResourceDefinition: the Flux CRD definition objects themselves
#   Kustomization, GitRepository: Flux CR instances (toolkit.fluxcd.io CRDs)
# These are skipped explicitly so the summary reflects them as "Skipped" not "Errors".
SKIP_KINDS="CustomResourceDefinition,Kustomization,GitRepository"

kubeconform \
    -skip "$SKIP_KINDS" \
    -ignore-missing-schemas \
    -summary \
    "$BUILD_OUTPUT"
