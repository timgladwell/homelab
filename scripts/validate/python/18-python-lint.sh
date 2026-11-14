#!/bin/bash
# ruff lint and format check over every Python file in the repo: the programs
# that ship, the scripts, the validation helpers and the tests. Config is in
# pyproject.toml. To fix: `ruff check --fix .` and `ruff format .`.
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"
# ruff's rules and formatting change between releases, so another version than
# CI's can pass a file CI fails, or the reverse.
source scripts/lib-pinned-tool.sh
require_pinned_tool ruff RUFF_VERSION || exit 1
ruff check --output-format concise .
rc=$?
ruff format --check . || rc=1
echo "CHECKED $(ruff check --show-files . | wc -l | tr -d ' ') files"
exit $rc
