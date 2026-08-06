#!/bin/bash
# Shared discovery helpers. Everything is derived from the repo layout and the
# real Flux Kustomization objects — there is no hand-maintained list of sites or
# layers anywhere in the validation pipeline, so it cannot drift from what Flux
# actually reconciles.

# Every site that has a sites/<site>/ directory.
sites() {
    local d
    for d in sites/*/; do
        [[ -d "$d" ]] || continue
        basename "$d"
    done
}

# For a site, print "<kustomization-name> <spec.path>" per line, read straight
# out of clusters/<site>/*.yaml. Only top-level files are scanned, so Flux's own
# clusters/<site>/flux-system/gotk-sync.yaml is correctly excluded.
site_kustomizations() {
    local site="$1" f
    for f in "clusters/${site}"/*.yaml; do
        [[ -f "$f" ]] || continue
        grep -qE '^kind: Kustomization$' "$f" || continue
        awk '
            /^metadata:/ { in_meta = 1; next }
            in_meta && /^  name:/ { name = $2; in_meta = 0 }
            /^  path:/ && !path { path = $2 }
            END { if (name && path) print name, path }
        ' "$f"
    done
}
