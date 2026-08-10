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

    # Resources that opt out of Flux substitution are dropped before scanning:
    # their ${...} tokens belong to the target application's own templating (a
    # Grafana dashboard's ${datasource}), not to Flux, and Flux never sees them.
    #
    # Everything left is scanned case-insensitively. Lowercase matters: Grafana
    # dashboards carry ${ds_prometheus}, and an uppercase-only pattern here meant
    # the whole class was invisible to this check. That is not cosmetic — Flux
    # v2.9 substitutes in strict mode, where one undefined variable fails the
    # entire Kustomization, so an unscanned ${var} is an outage waiting to merge.
    scannable=$(awk 'BEGIN { RS = "\n---\n" }
        !/kustomize\.toolkit\.fluxcd\.io\/substitute: disabled/' "$BUILD_OUTPUT")
    used=$(grep -oE '\$\{[A-Za-z_][A-Za-z0-9_]*\}' <<< "$scannable" | sort -u | sed 's/[${}]//g')

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
