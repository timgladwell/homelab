#!/bin/bash
# Every site needs Pi-hole's admin password in two namespaces.
#
# Secrets do not cross namespaces, so the password exists twice per site: the
# dns copy is read by Pi-hole itself and by pihole-sync, the landing copy by
# base/landing/app.py, which is what gives the household a way to pause
# blocking without the admin credential.
#
# The values must match and this cannot check that — they are encrypted to a
# key CI does not hold, which is the point. What it can check is that neither
# file is missing, which is the likelier mistake: a new site copied from an
# existing one, or a layer added without its secret. A missing file does not
# fail the build, because kustomize simply generates a Deployment referencing a
# Secret that is not there; the pod then sits in CreateContainerConfigError on
# the cluster, long after review.
#
# A mismatch in the *values* still gets through here. It surfaces as the
# landing page failing every call while Pi-hole itself is healthy — noted in
# docs/runbooks/pihole-access.md and in the bootstrap runbook.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

fail=0
found=0

for site_dir in sites/*/; do
    site="$(basename "$site_dir")"
    for layer in infrastructure apps; do
        file="sites/$site/$layer/pihole-secret.sops.yaml"
        found=$((found + 1))

        if [[ ! -f "$file" ]]; then
            echo "✗ $site: $file is missing"
            echo "    Pi-hole's password is needed in both the dns namespace"
            echo "    (infrastructure) and the landing namespace (apps)."
            fail=1
            continue
        fi

        # A file that exists but is not referenced is worse than a missing one:
        # it reads as done and reconciles nothing.
        if ! grep -q 'pihole-secret.sops.yaml' "sites/$site/$layer/kustomization.yaml"; then
            echo "✗ $site: $file exists but sites/$site/$layer/kustomization.yaml does not list it"
            fail=1
            continue
        fi

        echo "✓ $file"
    done
done

# Sites come from a glob; an empty result means the glob broke, not that the
# repo has no sites. The harness fails the step on CHECKED 0.
echo "CHECKED $found Pi-hole secret files"
exit $fail
