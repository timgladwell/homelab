#!/bin/bash
# Unit tests for the repo's Python programs and the tooling around them.
#
# Python that sits beside a kustomization.yaml ships to the clusters, as an
# opaque ConfigMap payload (today base/landing/app.py and
# base/pihole-sync/sync.py), so every manifest step would pass with a syntax
# error in it. All other Python is tooling for building and deploying, run on a
# developer's machine or in CI. Both are tested here: chart_sources.py, which
# step 15 and the weekly audit run, and the shell helpers other scripts lean
# on, such as scripts/lib-pinned-tool.sh, driven through subprocess.
#
# Both programs used to carry a hand-rolled self-check that nothing ever ran —
# `sync.py --self-check` tested exactly the right thing and had never been
# executed in CI. That is the failure mode this step exists to close.
#
# pytest is the runner (requirements-dev.in); the tests stay unittest-style
# classes, which it runs unchanged. Config is in pyproject.toml.
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

output=$(python3 -m pytest -v 2>&1)
status=$?
echo "$output"

# What pytest found, not what passed: a failing run still fails on $status.
# Not anchored: -v prints it as "collecting ... collected 76 items".
count=$(grep -oE 'collected [0-9]+ item' <<< "$output" | tail -1 | awk '{print $2}')

# Zero is not a legitimate pass: it means discovery stopped finding tests, not
# that there is nothing to check. The harness fails the step on CHECKED 0.
echo "CHECKED ${count:-0} unit tests"
exit $status
