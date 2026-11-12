#!/bin/bash
# require_pinned_tool <tool> <VAR>
#
# Fails unless the <tool> on PATH is the version .github/workflows/validate.yml
# pins as <VAR>, so a local run can't disagree with CI because of a different
# tool. For tools CI installs with pipx (yamllint, uv), as the
# development-environment runbook does too; each failure prints the exact
# command that fixes it. Assumes "<tool> --version" prints the version as its
# second field, as both do.
require_pinned_tool() {
    local tool="$1" var="$2" root pinned path have
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
        echo "  pipx install $tool==$pinned" >&2
        return 1
    fi
    have="$("$tool" --version | awk '{print $2}')"
    if [[ "$have" != "$pinned" ]]; then
        echo "ERROR: $tool $have is installed ($path), but CI pins $pinned. To match it:" >&2
        [[ -n "${setup:-}" ]] && echo "  $setup" >&2
        echo "  pipx install --force $tool==$pinned" >&2
        # pipx links into ~/.local/bin. Any other copy, from Homebrew say, can
        # come first on PATH and keep winning after the install.
        if [[ "$path" != "$HOME/.local/bin/$tool" ]]; then
            echo "  and remove the copy at $path (if from Homebrew: brew uninstall $tool)" >&2
        fi
        return 1
    fi
}
