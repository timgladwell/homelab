#!/bin/bash
# Validate each Flux Kustomization builds using the actual Kustomization objects.
# Runs in dry-run mode — no cluster connection required.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

fail=0
for ks in infrastructure infrastructure-config apps app-config; do
    case "$ks" in
        infrastructure)        path="./infrastructure/homelab" ;;
        infrastructure-config) path="./infrastructure-config/homelab" ;;
        apps)                  path="./apps/homelab" ;;
        app-config)            path="./app-config/homelab" ;;
    esac
    output=$(flux build kustomization "$ks" \
        --path "$path" \
        --kustomization-file "./clusters/homelab/${ks}.yaml" \
        --dry-run 2>&1)
    if [[ $? -ne 0 ]]; then
        echo "✗ $ks: $output"
        fail=1
    else
        echo "✓ $ks"
    fi
done

exit $fail
