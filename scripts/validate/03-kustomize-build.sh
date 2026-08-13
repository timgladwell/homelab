#!/bin/bash
# Build each site's complete manifest set into $K3S_BUILD_DIR/k3s-built-<site>.yaml
# for the schema, best-practice, variable and policy steps to consume.
#
# Assembled the same way Flux does it: the cluster entry point plus one build per
# Flux Kustomization, at the path that Kustomization actually declares. This
# replaces the old clusters/<site>-validation/ directories, which listed the
# same layers a second time and silently lied whenever the two drifted.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
source "$(dirname "$0")/lib-sites.sh"
BUILD_DIR="${K3S_BUILD_DIR:-${TMPDIR:-/tmp}}"

fail=0
checked=0
for site in $(sites); do
    out="${BUILD_DIR}/k3s-built-${site}.yaml"
    : > "$out"
    checked=$((checked + 1))

    # Flux bootstrap manifests, cluster-vars, and the Kustomization objects.
    if ! kustomize build "./clusters/${site}" >> "$out"; then
        echo "✗ [$site] kustomize build ./clusters/${site} failed"
        fail=1
    fi

    while read -r ks path; do
        [[ -n "$ks" ]] || continue
        checked=$((checked + 1))
        echo "---" >> "$out"
        if ! kustomize build "$path" >> "$out"; then
            echo "✗ [$site] kustomize build $path ($ks) failed"
            fail=1
        fi
    done < <(site_kustomizations "$site")
done
echo "CHECKED $checked builds"
exit $fail
