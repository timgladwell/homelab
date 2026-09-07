#!/bin/bash
# Unit tests for the repo's two Python programs.
#
# base/landing/app.py and base/pihole-sync/sync.py are the only executable code
# here — everything else is declarative and covered by the schema, policy and
# best-practice steps. Neither is rendered by kustomize in any meaningful sense:
# they ship as opaque ConfigMap payloads, so every other step in this pipeline
# would pass with a syntax error in either file.
#
# Both used to carry a hand-rolled self-check that nothing ever ran —
# `sync.py --self-check` tested exactly the right thing and had never been
# executed in CI. That is the failure mode this step exists to close, so the
# tests moved to tests/ and this runs them.
#
# stdlib unittest, no pytest: no new pin in .github/workflows/validate.yml and
# nothing to install, which matters more than any feature pytest would add for
# twenty tests with no fixtures.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

output=$(python3 -m unittest discover --start-directory tests --verbose 2>&1)
status=$?
echo "$output"

# unittest reports the count on stderr, which is folded into $output above.
count=$(grep -oE '^Ran [0-9]+ test' <<< "$output" | tail -1 | awk '{print $2}')

# Zero is not a legitimate pass: it means discovery stopped finding tests, not
# that there is nothing to check. The harness fails the step on CHECKED 0.
echo "CHECKED ${count:-0} unit tests"
exit $status
