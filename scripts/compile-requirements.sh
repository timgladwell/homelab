#!/bin/bash
# Compiles every requirements*.in (the direct dependencies, unpinned) into the
# requirements*.txt beside it: every version the install needs, transitive ones
# included, each with the hashes pip will verify.
#
#   scripts/compile-requirements.sh            re-lock after editing a .in file;
#                                              versions already in the lockfile stay
#   scripts/compile-requirements.sh --check    fail if a lockfile is out of step with
#                                              its .in (validation step 17)
#
# Pins move through Renovate: its pip-compile manager reads the command from
# each lockfile's header and re-runs it, so the options below are part of what
# it executes. Keep the header (no --no-header) and pass every source file
# explicitly. To hold a dependency back, constrain it in the .in file with a
# comment saying why; never edit a lockfile by hand.
#
# --python-version is the minor CI and the pods run (validate.yml, and the
# python image tag in base/pihole-sync/sync-job.yaml). Resolving for a different
# minor could pick versions the pod can't install.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

PYTHON_VERSION=3.15
mode="${1:-compile}"

# uv's output can change between versions, so a different uv here than in CI
# can make --check disagree with CI, or re-lock a file CI then calls stale.
# Equal to the pin or not at all, as the runbook asks for every tool.
pinned="$(sed -n 's/^ *UV_VERSION: *//p' .github/workflows/validate.yml)"
if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not found on PATH. Install uv $pinned." >&2
    echo "  e.g. pipx install uv==$pinned — CI pins it in .github/workflows/validate.yml" >&2
    exit 1
fi
have="$(uv --version | awk '{print $2}')"
if [[ -z "$pinned" || "$have" != "$pinned" ]]; then
    echo "ERROR: uv $have is installed, but .github/workflows/validate.yml pins ${pinned:-nothing}." >&2
    echo "  e.g. pipx install --force uv==$pinned" >&2
    exit 1
fi

compile() {
    local in="$1" out="$2"
    shift 2
    uv pip compile "$in" -o "$out" --generate-hashes --python-version "$PYTHON_VERSION" "$@"
}

checked=0
fail=0
while IFS= read -r in; do
    in="${in#./}"
    out="${in%.in}.txt"
    checked=$((checked + 1))

    case "$mode" in
        compile) compile "$in" "$out" --quiet || fail=1 ;;
        --check)
            if [[ ! -f "$out" ]]; then
                echo "✗ $out is missing: run scripts/compile-requirements.sh"
                fail=1
                continue
            fi
            # Start from the committed lockfile, so versions already pinned stay
            # and only a change to the .in file shows up. The first two lines are
            # the header, which names the output path.
            tmp="$(mktemp)"
            cp "$out" "$tmp"
            if ! compile "$in" "$tmp" --quiet; then
                fail=1
            elif ! diff <(tail -n +3 "$out") <(tail -n +3 "$tmp") >/dev/null; then
                echo "✗ $out is out of step with $in: run scripts/compile-requirements.sh"
                fail=1
            fi
            rm -f "$tmp"
            ;;
        *) echo "usage: $0 [--check]" >&2; exit 2 ;;
    esac
done < <(find . -name 'requirements*.in' -not -path './.venv/*' -not -path './.git/*' | sort)

[[ "$mode" == "--check" ]] && echo "CHECKED $checked requirements lockfiles"
exit $fail
