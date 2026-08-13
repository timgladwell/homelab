#!/bin/bash
# Scan for security misconfigurations with Trivy. All severities are errors —
# use .trivyignore.yaml to suppress checks that don't apply here, not a
# severity floor (a MEDIUM/LOW finding is still worth knowing about).
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
JSON="$(mktemp)"
ERR="$(mktemp)"
trap 'rm -f "$JSON" "$ERR"' EXIT

# .claude/ is Claude Code tooling (agent/skill defs, nested git worktrees),
# not cluster manifests — a worktree's content is scanned from within its
# own checkout, not by this scan reaching in from the main tree.
trivy config ./ --ignorefile .trivyignore.yaml --skip-dirs .claude --format json \
    > "$JSON" 2> "$ERR"

# Tool upgrade notices land on stderr and would otherwise be lost entirely —
# only failing steps get their output reported upstream, and a clean scan
# still deserves to surface "a newer trivy exists" to whoever's watching.
grep 'is now available' "$ERR" | sed 's/^/NOTICE: /'

fail=0

# Pinned to the SchemaVersion this script's jq queries below were written
# against. A jq query failing silently (empty output, not an error) on a
# reshaped field is the real risk with JSON scraping — this check turns a
# schema bump into a loud failure instead of a quietly-empty report.
schema_version=$(jq -r '.SchemaVersion' "$JSON") || {
    echo "ERROR: failed to parse trivy JSON output"
    exit 1
}
if [[ "$schema_version" != "2" ]]; then
    echo "ERROR: trivy JSON SchemaVersion is $schema_version, this script expects 2 — trivy output format may have changed, review the jq queries below"
    exit 1
fi

# A target with no checks run at all ("- Not scanned" in trivy's table view)
# hides a real coverage gap behind what looks like a clean pass.
not_scanned=$(jq -r '.Results[]? | select((.MisconfSummary.Successes // 0) == 0 and (.MisconfSummary.Failures // 0) == 0) | .Target' "$JSON") || {
    echo "ERROR: failed to parse trivy JSON output — schema may have changed"
    exit 1
}
if [[ -n "$not_scanned" ]]; then
    echo "Not scanned (no checks ran):"
    echo "$not_scanned" | sed 's/^/  /'
    fail=1
fi

# One line per finding instead of trivy's full remediation text and code
# snippets — file/rule/severity/title is enough to act on; the avd.aquasec.com
# link trivy prints per-finding has the rest if it's needed.
findings=$(jq -r '.Results[]? | .Target as $t | .Misconfigurations[]? | select(.Status=="FAIL") | "\($t): \(.ID) [\(.Severity)] \(.Title)"' "$JSON") || {
    echo "ERROR: failed to parse trivy JSON output — schema may have changed"
    exit 1
}
if [[ -n "$findings" ]]; then
    echo "$findings"
    fail=1
fi

exit $fail
