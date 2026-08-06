#!/bin/bash
# Validate each site's Flux Kustomization builds using the actual Kustomization objects.
# Runs in dry-run mode — no cluster connection required.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

fail=0

check() {
    local site="$1" ks="$2" path="$3"
    local output
    output=$(flux build kustomization "$ks" \
        --path "$path" \
        --kustomization-file "./clusters/${site}/${ks}.yaml" \
        --dry-run 2>&1)
    if [[ $? -ne 0 ]]; then
        echo "✗ [$site] $ks: $output"
        fail=1
    else
        echo "✓ [$site] $ks"
    fi
}

# Akron: full stack (shared core + akron-only monitoring + apps)
check akron infrastructure ./infrastructure/core
check akron infrastructure-akron-only ./infrastructure/akron-only
check akron infrastructure-config ./infrastructure-config/core
check akron apps ./apps/homelab
check akron app-config ./app-config/core

# Eastbank: shared core (patched with its own pihole values) + pihole-sync only
check eastbank infrastructure ./infrastructure/core-overlays/eastbank
check eastbank infrastructure-config ./infrastructure-config/core
check eastbank app-config ./app-config/core

exit $fail
