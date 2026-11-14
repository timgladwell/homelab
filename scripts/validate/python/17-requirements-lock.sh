#!/bin/bash
# Every requirements*.in has a lockfile that matches it.
#
# The lockfiles pin every version the pod and CI install, hashes included, so a
# .in file edited without re-locking would leave CI installing versions nobody
# asked for, or leave the new dependency out. This recompiles each one from the
# committed lockfile and fails if the result differs. The logic and the
# explanation of the options are in scripts/compile-requirements.sh.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
exec "$REPO_ROOT/scripts/compile-requirements.sh" --check
