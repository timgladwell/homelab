#!/bin/bash
# Build kustomizations and write output to /tmp/k3s-built.yaml.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
kustomize build ./clusters/homelab > "${K3S_BUILD_OUTPUT:-${TMPDIR:-/tmp}/k3s-built.yaml}"
