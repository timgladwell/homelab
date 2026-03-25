#!/bin/bash
# Check Kubernetes best practices against /tmp/k3s-built.yaml.
BUILD_OUTPUT="${K3S_BUILD_OUTPUT:-${TMPDIR:-/tmp}/k3s-built.yaml}"
if [[ ! -f "$BUILD_OUTPUT" ]]; then
    echo "ERROR: $BUILD_OUTPUT not found — run 02-kustomize-build.sh first" >&2
    exit 1
fi
kube-score score "$BUILD_OUTPUT"
