#!/bin/bash
# Validate YAML syntax across the repository.
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"
# yamllint's rules change between releases, so another version than CI's can
# pass a file CI fails, or the reverse.
source scripts/lib-pinned-tool.sh
require_pinned_tool yamllint YAMLLINT_VERSION || exit 1
yamllint --strict -f parsable ./
rc=$?
# yamllint reports no count of its own; --list-files is what it would have
# linted, and applies the same .yamllint ignore rules.
echo "CHECKED $(yamllint --list-files ./ | wc -l | tr -d ' ') files"
exit $rc
