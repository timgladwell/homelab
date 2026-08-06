#!/bin/bash
# Every *secret.sops.yaml in the repo must actually be SOPS-encrypted.
#
# scripts/setup-git-hooks.sh installs a pre-commit hook that checks this, but it
# only sees staged files, only runs for people who ran the setup script, is
# skippable with --no-verify, and never runs at all on Renovate or Dependabot
# PRs. "No secrets in the repo" is the repo's first design principle, so it gets
# an enforcement point that runs on every change regardless of who made it.
#
# Checks the whole tree rather than a diff: a file that was never encrypted does
# not stop being a problem just because this PR did not touch it.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

fail=0
found=0

while read -r file; do
    [[ -n "$file" ]] || continue
    found=$((found + 1))

    # $patch: delete manifests carry no data — nothing to encrypt.
    if grep -q '^\$patch: delete' "$file"; then
        echo "✓ $file (delete-only patch, no data)"
        continue
    fi

    if ! grep -q '^sops:' "$file"; then
        echo "✗ $file has no sops: metadata — it is NOT encrypted"
        fail=1
        continue
    fi

    if ! grep -q 'ENC\[' "$file"; then
        echo "✗ $file has sops: metadata but no ENC[] values"
        echo "    Either it has no data:/stringData: fields, or .sops.yaml's"
        echo "    encrypted_regex does not match them. Both mean plaintext."
        fail=1
        continue
    fi

    echo "✓ $file"
done <<< "$(git ls-files '*secret*.yaml' '*secret*.yml')"

if [[ $found -eq 0 ]]; then
    echo "No secret files found — nothing to check."
fi

exit $fail
