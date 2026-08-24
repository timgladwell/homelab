#!/bin/bash
# Build each site's complete manifest set into $K3S_BUILD_DIR/k3s-built-<site>.yaml
# for the schema, best-practice, variable and policy steps to consume.
#
# Assembled the same way Flux does it: the cluster entry point plus one build per
# Flux Kustomization, at the path that Kustomization actually declares. This
# replaces the old clusters/<site>-validation/ directories, which listed the
# same layers a second time and silently lied whenever the two drifted.
#
# The output is then hydrated the way Flux's postBuild.substituteFrom does at
# reconcile time, so every downstream step sees the values that actually get
# applied instead of ${METALLB_TRAEFIK_IP} where an IP belongs. `flux build
# --dry-run` cannot do this for us: with no cluster to read the ConfigMaps
# from, it emits the placeholders untouched.
#
# Which ConfigMaps those are is not passed in — substitute.py reads the
# substituteFrom lists out of this very output, so it uses whatever each site
# actually declares (today clusters/common/network-vars.yaml plus that site's
# cluster-vars.yaml).
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

    # Substitute in place. Documents annotated substitute: disabled are left
    # alone — their ${...} tokens belong to the target application (a Grafana
    # dashboard's ${DS_PROMETHEUS}), and Flux never touches them either.
    # Plain string replacement, deliberately: Flux's envsubst also supports
    # ${VAR:=default}, nothing here uses it, and anything this pass leaves
    # behind is what step 7 fails on.
    if ! python3 "$(dirname "$0")/substitute.py" "$out"; then
        echo "✗ [$site] variable substitution failed"
        fail=1
    fi
done
echo "CHECKED $checked builds"
exit $fail
