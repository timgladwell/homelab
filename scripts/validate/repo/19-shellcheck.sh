#!/bin/bash
# Lints every shell script in the repo with shellcheck: the validation steps and
# their libraries, the operator scripts, .github/scripts. Config, and the one
# repo-wide exception, are in .shellcheckrc; any other exception is an inline
# `# shellcheck disable=SCnnnn` with its reason beside it.
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT" || exit 1
# Checks change between shellcheck releases, so another version than CI's can
# pass a script CI fails, or the reverse. (No comment line here may begin with
# the tool's name: shellcheck parses those as directives.)
source scripts/lib-pinned-tool.sh
require_pinned_tool shellcheck SHELLCHECK_VERSION shellcheck-py || exit 1
# shortcut: *.sh only; no extensionless shell script exists today. One that is
# added goes unchecked until this also selects files by shebang.
git ls-files -z '*.sh' | xargs -0 shellcheck -f gcc
rc=$?
echo "CHECKED $(git ls-files '*.sh' | wc -l | tr -d ' ') files"
exit $rc
