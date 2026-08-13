#!/bin/bash
# Validate YAML syntax across the repository.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
yamllint --strict -f parsable ./
rc=$?
# yamllint reports no count of its own; --list-files is what it would have
# linted, and applies the same .yamllint ignore rules.
echo "CHECKED $(yamllint --list-files ./ | wc -l | tr -d ' ') files"
exit $rc
