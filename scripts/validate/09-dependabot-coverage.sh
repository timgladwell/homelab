#!/bin/bash
# Every directory holding a pinned container image must be covered by a
# Dependabot docker entry, or that image silently stops receiving update PRs.
#
# This is not hypothetical: the multi-site restructure moved directories out
# from under .github/dependabot.yml's paths, and Dependabot quietly stopped
# proposing bumps for pihole, unbound, python and system-upgrade-controller.
# Nothing failed, so nothing was noticed.
#
# clusters/*/flux-system is excluded on purpose — Flux's own controller images
# are upgraded through the Flux CLI, not by bumping tags in git.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

CONFIG=".github/dependabot.yml"

# Directories listed in dependabot.yml, normalised to repo-relative paths.
listed=$(grep -oE '^[[:space:]]+- "/[^"]*"' "$CONFIG" \
    | sed -E 's/^[[:space:]]*-[[:space:]]*"//; s/"$//; s|^/||' | sort -u)

# Directories containing an image: with an explicit tag. Untagged images
# (e.g. plans.yaml's rancher/k3s-upgrade) carry no version for Dependabot to
# bump, so they are correctly out of scope.
IMAGE_RE='^[[:space:]]*(-[[:space:]]+)?image:[[:space:]]*"?[^ "]+:[^ "]+'
found=$(grep -rlE "$IMAGE_RE" base/ sites/ --include='*.yaml' 2>/dev/null \
    | xargs -n1 dirname 2>/dev/null | sort -u)

fail=0
checked=0
while read -r dir; do
    [[ -n "$dir" ]] || continue
    checked=$((checked + 1))
    if ! grep -qxF "$dir" <<< "$listed"; then
        echo "✗ $dir has a pinned image but is not in $CONFIG"
        grep -nE "$IMAGE_RE" "$dir"/*.yaml | sed 's/^/    /'
        fail=1
    else
        echo "✓ $dir"
    fi
done <<< "$found"

# A listed directory that no longer exists means the config has gone stale the
# other way, which is how the last breakage started.
while read -r dir; do
    [[ -n "$dir" ]] || continue
    # Glob entries (the github-actions "/**") aren't real paths.
    [[ "$dir" == *'*'* ]] && continue
    if [[ ! -d "$dir" ]]; then
        echo "✗ $CONFIG lists $dir, which does not exist"
        fail=1
    fi
done <<< "$listed"

# Directories with a pinned image. Zero means the image-matching regex stopped
# matching — which would otherwise read as "every image is covered".
echo "CHECKED $checked image directories"
exit $fail
