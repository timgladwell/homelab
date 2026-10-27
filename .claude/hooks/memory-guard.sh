#!/usr/bin/env bash
# PreToolUse hook: ask before Claude writes a memory file, so a decision never
# lands somewhere the repo can't see. The rule is CONTRIBUTING.md, "Claude's
# memory". MEMORY.md is only the index, so editing it is not asked about.
set -euo pipefail

path=$(jq -r '.tool_input.file_path // empty')
case "$path" in
  */.claude/projects/*/memory/MEMORY.md) exit 0 ;;
  */.claude/projects/*/memory/*) ;;
  *) exit 0 ;;
esac

jq -n --arg path "$path" '{hookSpecificOutput: {
  hookEventName: "PreToolUse",
  permissionDecision: "ask",
  permissionDecisionReason: ("Memory write: " + $path + ". Memory holds only how Tim prefers to work with Claude. A decision, convention or technical fact goes in the repo (CONTRIBUTING.md, \"Where things live\") instead.")
}}'
