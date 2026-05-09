#!/bin/bash
# Check that every ${VAR} reference in the built manifest is defined in cluster-vars.yaml.
# Flux substitutes these at apply time; this step ensures no typos or missing entries.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_OUTPUT="${K3S_BUILD_OUTPUT:-${TMPDIR:-/tmp}/k3s-built.yaml}"

if [[ ! -f "$BUILD_OUTPUT" ]]; then
    echo "ERROR: $BUILD_OUTPUT not found — run 02-kustomize-build.sh first" >&2
    exit 1
fi

CLUSTER_VARS="${REPO_ROOT}/clusters/homelab/cluster-vars.yaml"

# All ${VAR} references in the built manifest (uppercase + underscore convention)
used=$(grep -oE '\$\{[A-Z_][A-Z0-9_]*\}' "$BUILD_OUTPUT" | sort -u | sed 's/[${}]//g')

if [[ -z "$used" ]]; then
    echo "No \${VAR} references found in manifest — nothing to check."
    exit 0
fi

# All keys defined in cluster-vars.yaml (lines matching uppercase key pattern under data:)
defined=$(grep -E '^\s+[A-Z_][A-Z0-9_]+:' "$CLUSTER_VARS" | sed 's/:.*//' | tr -d ' ' | sort -u)

undefined=$(comm -23 <(echo "$used") <(echo "$defined"))

if [[ -n "$undefined" ]]; then
    echo "ERROR: Variables referenced in manifests but not defined in cluster-vars.yaml:"
    echo "$undefined" | sed 's/^/  - /'
    exit 1
fi

echo "All \${VAR} references are defined in cluster-vars.yaml:"
echo "$used" | sed 's/^/  ✓ /'
