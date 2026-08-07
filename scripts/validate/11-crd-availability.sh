#!/bin/bash
# A Flux Kustomization with no `dependsOn` runs first, against whatever the
# cluster already has. It can therefore only use custom resources whose CRDs it
# installs itself, or that Flux provides. Anything else works on a cluster where
# the CRD happens to already exist, and fails on a fresh one:
#
#   IngressRoute/traefik/traefik-dashboard dry-run failed:
#   no matches for kind "IngressRoute" in version "traefik.io/v1alpha1"
#
# CRDs that arrive with a Helm chart do not count as self-installed: the chart is
# rendered by the cluster at reconcile time, so the CRD does not exist during
# that same Kustomization's dry-run. Put such resources in a later layer that
# dependsOn the one installing the chart — see base/traefik-routes/.
#
# This has shipped twice (the PiHole IngressRoute, then the Traefik dashboard
# one), each time only failing on a site being brought up from scratch, which is
# the worst place to find out.
#
# Scope: only dependency-free Kustomizations are checked. Later layers can rely
# on CRDs from the layers they depend on, and resolving that properly would mean
# walking the dependency graph for little benefit — the first layer is where
# this bug actually happens.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
source "$(dirname "$0")/lib-sites.sh"

# API groups every cluster has, plus the toolkit groups Flux itself installs
# before any of these Kustomizations are reconciled.
BUILTIN='^(v1|apps|batch|policy|autoscaling|rbac\.authorization\.k8s\.io|networking\.k8s\.io|storage\.k8s\.io|apiextensions\.k8s\.io|admissionregistration\.k8s\.io|coordination\.k8s\.io|discovery\.k8s\.io|scheduling\.k8s\.io|node\.k8s\.io|certificates\.k8s\.io|events\.k8s\.io|(helm|kustomize|source|notification|image)\.toolkit\.fluxcd\.io)$'

fail=0

for site in $(sites); do
    while read -r ks path; do
        [[ -n "$ks" ]] || continue

        # Only Kustomizations that run first, with nothing to depend on.
        grep -q '^  dependsOn:' "clusters/${site}/${ks}.yaml" && continue

        built=$(kustomize build "$path" 2>/dev/null) || {
            echo "✗ [$site] $ks: kustomize build $path failed"
            fail=1
            continue
        }

        # API groups this build installs CRDs for. A CRD's metadata.name is
        # "<plural>.<group>", so the group is everything after the first dot.
        provided=$(awk '
            /^kind: CustomResourceDefinition$/ { crd = 1; next }
            crd && /^  name:/ { sub(/^[^.]*\./, "", $2); print $2; crd = 0 }
        ' <<< "$built" | sort -u)

        # API groups this build uses.
        used=$(grep -E '^apiVersion:' <<< "$built" \
            | awk '{sub(/\/.*$/, "", $2); print $2}' | sort -u)

        missing=""
        while read -r group; do
            [[ -n "$group" ]] || continue
            [[ "$group" =~ $BUILTIN ]] && continue
            grep -qxF "$group" <<< "$provided" && continue
            missing+="$group"$'\n'
        done <<< "$used"

        if [[ -n "$missing" ]]; then
            echo "✗ [$site] $ks ($path) uses API groups whose CRDs it does not install:"
            while read -r group; do
                [[ -n "$group" ]] || continue
                echo "    $group"
                grep -A1 "^apiVersion: ${group}/" <<< "$built" \
                    | grep -E '^kind:' | sort -u | sed 's/^/      /'
            done <<< "$missing"
            echo "    Move these into a layer that dependsOn $ks."
            fail=1
        else
            echo "✓ [$site] $ks"
        fi
    done < <(site_kustomizations "$site")
done

exit $fail
