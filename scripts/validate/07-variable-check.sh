#!/bin/bash
# Check that every ${VAR} reference in each site's built manifest is defined in
# that site's cluster-vars.yaml. Flux substitutes these at apply time; this step
# ensures no typos or missing entries. Checked per-site because sites define
# different variable sets.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="${K3S_BUILD_DIR:-${TMPDIR:-/tmp}}"
cd "$REPO_ROOT"
source "$(dirname "$0")/lib-sites.sh"

fail=0
for site in $(sites); do
    BUILD_OUTPUT="${BUILD_DIR}/k3s-built-${site}.yaml"
    if [[ ! -f "$BUILD_OUTPUT" ]]; then
        echo "ERROR: $BUILD_OUTPUT not found — run 03-kustomize-build.sh first" >&2
        exit 1
    fi
    CLUSTER_VARS="${REPO_ROOT}/clusters/${site}/cluster-vars.yaml"

    # All ${VAR} references in the built manifest (uppercase + underscore convention).
    # Exclude DS_* variables — Grafana datasource UID template variables in dashboard JSON
    # ConfigMaps; they use the same ${} syntax but are not Flux substitution variables.
    # Flux leaves undefined variables as-is, so these are safe to skip here.
    used=$(grep -oE '\$\{[A-Z_][A-Z0-9_]*\}' "$BUILD_OUTPUT" | sort -u | sed 's/[${}]//g' | grep -v '^DS_')

    if [[ -z "$used" ]]; then
        echo "[$site] No \${VAR} references found — nothing to check."
        continue
    fi

    # All keys defined in cluster-vars.yaml (lines matching uppercase key pattern under data:)
    defined=$(grep -E '^\s+[A-Z_][A-Z0-9_]+:' "$CLUSTER_VARS" | sed 's/:.*//' | tr -d ' ' | sort -u)

    undefined=$(comm -23 <(echo "$used") <(echo "$defined"))

    if [[ -n "$undefined" ]]; then
        echo "[$site] ERROR: Variables referenced in manifests but not defined in cluster-vars.yaml:"
        echo "$undefined" | sed 's/^/  - /'
        fail=1
    else
        echo "[$site] All \${VAR} references are defined in cluster-vars.yaml:"
        echo "$used" | sed 's/^/  ✓ /'
    fi
done
exit $fail
