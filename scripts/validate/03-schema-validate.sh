#!/bin/bash
# Validate Kubernetes schemas against /tmp/k3s-built.yaml.
BUILD_OUTPUT="${K3S_BUILD_OUTPUT:-${TMPDIR:-/tmp}/k3s-built.yaml}"
if [[ ! -f "$BUILD_OUTPUT" ]]; then
    echo "ERROR: $BUILD_OUTPUT not found — run 02-kustomize-build.sh first" >&2
    exit 1
fi
kubeconform -summary "$BUILD_OUTPUT"
