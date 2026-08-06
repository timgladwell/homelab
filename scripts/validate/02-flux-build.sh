#!/bin/bash
# Validate every Flux Kustomization builds, using the actual Kustomization
# objects and their declared spec.path. Runs in dry-run mode — no cluster
# connection required.
#
# Because the path comes from the object rather than a copy kept in this
# script, a spec.path pointing at a directory that doesn't exist fails here
# instead of on the cluster after merge.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
source "$(dirname "$0")/lib-sites.sh"

fail=0

for site in $(sites); do
    while read -r ks path; do
        [[ -n "$ks" ]] || continue
        if ! output=$(flux build kustomization "$ks" \
            --path "$path" \
            --kustomization-file "./clusters/${site}/${ks}.yaml" \
            --dry-run 2>&1); then
            echo "✗ [$site] $ks ($path): $output"
            fail=1
        else
            echo "✓ [$site] $ks ($path)"
        fi
    done < <(site_kustomizations "$site")
done

exit $fail
