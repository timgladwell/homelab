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
# --skip-version-check: keeping trivy current is Renovate's job, driven by the
# `# renovate:` comment on the pin in .github/workflows/validate.yml. Scraping
# "a newer trivy is available" out of stderr answered the wrong question anyway
# — it compared local against latest, when what matters is local against the
# version CI will use — and it would have silently stopped matching the day
# Aqua reworded the notice.
trivy config ./ --ignorefile .trivyignore.yaml --skip-dirs .claude \
    --skip-version-check --format json > "$JSON" 2> "$ERR"
rc=$?

# No --exit-code is passed, so trivy returns 0 even with findings (they're read
# out of the JSON below). Any non-zero status is therefore a real tool failure —
# a missing ignorefile, a bad flag — and the explanation is on stderr. Without
# this, such a run leaves empty stdout, `jq -r .SchemaVersion` on an empty file
# succeeds with empty output, and the script reports a bogus schema change while
# the actual FATAL sits unread in $ERR.
if [[ $rc -ne 0 ]]; then
    echo "ERROR: trivy exited $rc"
    cat "$ERR"
    exit 1
fi

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

# Number of targets trivy actually scanned. A scan that matches no files exits 0
# and emits schema-2 JSON with no Results key at all, which would otherwise be a
# silent PASS — an over-broad --skip-dirs or a cwd change turns the whole step
# into a no-op. The harness fails any step reporting 0 here.
echo "CHECKED $(jq -r '.Results | length // 0' "$JSON") targets"

exit $fail
