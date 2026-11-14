#!/bin/bash
# require_pinned_tool <tool> <VAR> [<package>]
#
# Fails unless the <tool> on PATH is the version .github/workflows/validate.yml
# pins as <VAR>, so a local run can't disagree with CI because of a different
# tool. For tools CI installs with pipx (yamllint, uv, ruff, shellcheck), as
# the development-environment runbook does too; each failure prints the exact
# command that fixes it.
#
# <package> is the PyPI name when it isn't <tool>: shellcheck ships as the
# wrapper shellcheck-py. Such a wrapper's version can carry one more component than
# the tool's own (shellcheck-py 0.11.0.1 is shellcheck 0.11.0), so for a
# wrapper only, the pin may extend the installed version by a trailing
# component; otherwise the two must be equal. The installed version
# is the first version-shaped string "<tool> --version" prints, which covers
# "ruff 0.17.5" and shellcheck's banner with "version: 0.11.0" on line two.
require_pinned_tool() {
    local tool="$1" var="$2" package="${3:-$1}" root pinned path have
    root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    pinned="$(sed -n "s/^ *${var}: *//p" "$root/.github/workflows/validate.yml")"
    if [[ -z "$pinned" ]]; then
        echo "ERROR: could not read ${var} from .github/workflows/validate.yml" >&2
        return 1
    fi
    if ! command -v pipx > /dev/null 2>&1; then
        local setup="brew install pipx && pipx ensurepath   # then open a new shell"
    fi
    if ! path="$(command -v "$tool")"; then
        echo "ERROR: $tool not found on PATH. Install the version CI pins:" >&2
        [[ -n "${setup:-}" ]] && echo "  $setup" >&2
        echo "  pipx install $package==$pinned" >&2
        return 1
    fi
    have="$("$tool" --version | grep -oE '[0-9]+(\.[0-9]+)+' | head -1)"
    if [[ "$have" != "$pinned" ]] && ! [[ "$package" != "$tool" && "$pinned" == "$have".[0-9]* ]]; then
        echo "ERROR: $tool $have is installed ($path), but CI pins $package $pinned. To match it:" >&2
        [[ -n "${setup:-}" ]] && echo "  $setup" >&2
        echo "  pipx install --force $package==$pinned" >&2
        # pipx links into ~/.local/bin. Any other copy, from Homebrew say, can
        # come first on PATH and keep winning after the install.
        if [[ "$path" != "$HOME/.local/bin/$tool" ]]; then
            echo "  and remove the copy at $path (if from Homebrew: brew uninstall $tool)" >&2
        fi
        return 1
    fi
}
