#!/bin/bash
# Render every HelmRelease with flate, per site, and check the rendered output.
#
# Every other step stops at the HelmRelease: kustomize, flux build and
# kubeconform all treat `values:` as opaque YAML, so a chart only renders on the
# cluster, after merge. Three bugs shipped through that gap (#213), one of them
# for months: a Loki values key the v7 chart no longer read, silently dropping
# retention. This step renders each chart the way helm-controller will and fails
# on:
#
#   - a chart or version that cannot be fetched,
#   - a template error, or values rejected by the chart's values.schema.json
#     (helm validates against it on render, so an invented key fails here for
#     every chart that ships a schema), and
#   - a policy in policy/ under package homelab.rendered, which asserts that
#     settings whose silent loss costs something survive into the rendered
#     objects. That is the only way to catch "the chart stopped reading this
#     key", which renders cleanly by definition.
#
# Unlike every other step this one needs the network, to fetch chart indexes
# and charts. A fetch failure still fails the step, but is labelled as one, so
# "re-run it" and "fix the PR" never look alike. flate's own on-disk cache
# (FLATE_CACHE_DIR, which the CI setup action caches across runs) keeps the chart
# downloads themselves off the network after the first run.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="${K3S_BUILD_DIR:-${TMPDIR:-/tmp}}"
cd "$REPO_ROOT"
source "$(dirname "$0")/lib-sites.sh"

# GNU timeout. Standard on Linux; on macOS it comes with coreutils.
if ! command -v timeout >/dev/null 2>&1; then
    echo "ERROR: timeout not found on PATH. On macOS: brew install coreutils" >&2
    exit 1
fi

if ! command -v flate >/dev/null 2>&1; then
    echo "ERROR: flate not found on PATH." >&2
    echo "  brew install --cask home-operations/tap/flate" >&2
    echo "  CI pins the flate action in .github/workflows/validate.yml; stay at or above it." >&2
    exit 1
fi

# Always render the full tree. flate's setup action exports FLATE_BASE (the
# default branch) for every later step, which switches flate into changed-only
# mode: a PR would render only the releases it touches, and a push to main
# would render nothing at all. A k3s bump or a policy change must re-render
# every release, and the whole tree takes seconds.
unset FLATE_BASE FLATE_PATH_ORIG

# Render against the Kubernetes version the clusters are pinned to, so a k3s
# bump PR re-renders every chart at the new version: helm enforces a chart's
# kubeVersion constraint, and templates branch on .Capabilities.KubeVersion.
# flate's own default is whatever it was built with, which is neither.
kube_version=$(grep -m1 -E '^  version: v[0-9]' base/system-upgrade-controller/plans.yaml \
    | sed -E 's/^  version: v([0-9.]+)\+.*/\1/')
if [[ -z "$kube_version" ]]; then
    echo "ERROR: could not read the k3s version from base/system-upgrade-controller/plans.yaml" >&2
    exit 1
fi
echo "Rendering at Kubernetes $kube_version (from the system-upgrade-controller plan)"

# flate reports one failure per object as "  ✗  <Kind>  <ns/name>  <message>",
# with multi-line messages (schema errors) continued on indented lines. There is
# no machine-readable error format, so this parses that text — and if it ever
# stops matching, a non-zero exit with nothing classified prints the raw output
# and still fails, rather than passing silently.
classify() {
    awk '
        function flush() {
            if (!kind) return
            # "no such host" is deliberately not in the network list: it is
            # far more often a mistyped repository URL than a DNS outage.
            if (kind ~ /^(HelmRepository|HelmChart|OCIRepository|GitRepository|Bucket)$/) {
                tag = (msg ~ /timeout|connection (refused|reset)|TLS handshake|status code 5[0-9][0-9]|EOF/) \
                    ? "FETCH (network — likely transient, re-run)" \
                    : "FETCH (source — check the chart name, version or repository URL)"
            } else if (kind == "HelmRelease" && msg ~ /chart source .* not ready/) {
                kind = ""; return   # the source failure above already reports this
            } else {
                tag = "RENDER"
            }
            printf "  %s: %s %s\n    %s\n", tag, kind, obj, msg
            n++; kind = ""
        }
        /^  ✗  [A-Za-z]+  / {
            flush()
            line = $0; sub(/^  ✗  /, "", line)
            kind = line; sub(/  .*/, "", kind)
            rest = substr(line, length(kind) + 3)
            obj = rest; sub(/  .*/, "", obj)
            msg = substr(rest, length(obj) + 3)
            next
        }
        /^  ✗ [0-9]+ failed/ { flush(); next }
        kind && /^     / { m = $0; sub(/^ +/, "", m); msg = msg "\n    " m; next }
        END { flush(); exit(n ? 0 : 1) }
    '
}

# flate has no overall deadline of its own, and a chart fetch has hung CI
# for minutes with no output at all (#385). A normal cold render takes seconds.
# After the deadline flate gets TERM, then KILL 10s later: it shuts down
# gracefully on TERM, which waits out any fetch in flight, and a fetch that
# never returns would wait forever. `timeout` exits 124 on the deadline and
# 137 if it had to KILL.
render_timeout=300

fail=0
checked=0
for site in $(sites); do
    echo "--- $site ---"
    rendered="${BUILD_DIR}/k3s-rendered-${site}.yaml"
    errors="$(mktemp)"

    # Debug logging costs a couple of dozen lines and is only printed on a
    # timeout, where its last lines name the fetch or render still in flight.
    # Its closing "reconcile complete … helm_releases=N" line is also the
    # count for CHECKED, so the tree is rendered once rather than asked again.
    timeout --kill-after=10 "$render_timeout" flate build hr -p "clusters/${site}" --kube-version "$kube_version" \
        --no-progress --log-level debug > "$rendered" 2> "$errors"
    rc=$?

    if [[ $rc -eq 0 ]]; then
        echo "✓ rendered $(grep -c '^kind:' "$rendered") objects"
        conftest test "$rendered" \
            --policy "$REPO_ROOT/policy" \
            --namespace homelab.rendered \
            --fail-on-warn \
            --no-color || fail=1
    elif [[ $rc -eq 124 || $rc -eq 137 ]]; then
        fail=1
        echo "  TIMEOUT: flate did not finish within ${render_timeout}s — most likely a chart fetch"
        echo "  that stalled; re-run. Its last log lines, naming what was in flight:"
        tail -n 10 "$errors" | sed 's/^/    /'
    else
        fail=1
        if ! grep -v '^time=' "$errors" | classify; then
            echo "  FAIL (unclassified) — flate's raw output:"
            cat "$errors"
        fi
    fi

    # Absent when flate was killed before finishing; the step has failed then
    # anyway, and the arithmetic must not abort the remaining sites.
    rendered_hrs=$(sed -n 's/.*msg="reconcile complete".* helm_releases=\([0-9]*\).*/\1/p' "$errors" | tail -1)
    checked=$((checked + ${rendered_hrs:-0}))
    rm -f "$errors"
done

echo "CHECKED $checked HelmReleases"
exit $fail
