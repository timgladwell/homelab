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

# One check per Flux Kustomization per site — the path must match spec.path
# in clusters/<site>/<ks>.yaml. Stays explicit because each site decides which
# layers it has (Eastbank has no monitoring or apps).
check akron infrastructure ./sites/akron/infrastructure
check akron infrastructure-akron-only ./sites/akron/monitoring
check akron infrastructure-config ./sites/akron/infrastructure-config
check akron apps ./sites/akron/apps
check akron app-config ./sites/akron/app-config

check eastbank infrastructure ./sites/eastbank/infrastructure
check eastbank infrastructure-config ./sites/eastbank/infrastructure-config
check eastbank app-config ./sites/eastbank/app-config

exit $fail
