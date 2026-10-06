#!/bin/bash
# Trivy over the chart-rendered objects from step 15, judged against the same
# decisions as the cluster audit (scripts/trivy-accepted-findings.txt).
#
# Step 6 scans the files in git, where a chart is one HelmRelease, so nothing a
# chart renders was scanned before it reached a cluster: Prometheus, Grafana,
# Loki, cert-manager, MetalLB, every RBAC object those charts ship. The
# trivy-operator audit sees them, but only after they are deployed, and only
# when someone runs a cycle.
#
# The two scans ask the same question about the same objects, so they share one
# list of answers. To make that possible, each rendered object is scanned as its
# own file named the way trivy-operator names its reports —
# <namespace>/<kind>-<name>, or <kind>-<name> for a cluster-scoped object — and
# filtered by the same matcher the audit uses. A Deployment is named
# "replicaset-", because that is the object trivy-operator reports on, so one
# accepted line covers the rendered Deployment and the live ReplicaSet.
# Splitting also gives each finding its object in the name, which a scan of the
# one multi-document file does not.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="${K3S_BUILD_DIR:-${TMPDIR:-/tmp}}"
cd "$REPO_ROOT"
source "$(dirname "$0")/lib-sites.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail=0
checked=0
for site in $(sites); do
    echo "--- $site ---"
    rendered="${BUILD_DIR}/k3s-rendered-${site}.yaml"
    if [[ ! -s "$rendered" ]]; then
        echo "ERROR: $rendered is missing or empty — step 15 must render it first"
        fail=1
        continue
    fi

    # flate emits one object per "---" document with top-level keys sorted, so
    # kind, metadata.name and metadata.namespace are each at a fixed indent.
    # name/namespace are only taken inside the top-level metadata block, not
    # from a pod template's or a roleRef's.
    split="$WORK/$site"
    mkdir -p "$split"
    awk -v out="$split" '
        function flush(   k, dir, f) {
            if (kind != "" && name != "") {
                k = tolower(kind)
                if (k == "deployment") k = "replicaset"
                dir = (ns != "") ? out "/" ns : out
                system("mkdir -p \"" dir "\"")
                f = dir "/" k "-" name ".yaml"
                printf "%s", buf > f
                close(f)
            }
            buf = ""; kind = ""; name = ""; ns = ""; top = ""
        }
        /^---$/ { flush(); next }
        { buf = buf $0 "\n" }
        /^[A-Za-z]/ { top = $1 }
        /^kind: / { kind = $2 }
        top == "metadata:" && /^  name: / { name = $2 }
        top == "metadata:" && /^  namespace: / { ns = $2 }
        END { flush() }
    ' "$rendered"

    json="$WORK/$site.json"
    err="$WORK/$site.err"
    # --skip-version-check: see step 6. No ignorefile: exceptions for these
    # objects live in the accepted list, applied below, not in .trivyignore.yaml,
    # whose path-keyed entries are about files in git.
    (cd "$split" && trivy config . --skip-version-check --format json) > "$json" 2> "$err"
    rc=$?
    # As in step 6: without --exit-code, a non-zero exit is a tool failure.
    if [[ $rc -ne 0 ]]; then
        echo "ERROR: trivy exited $rc"
        cat "$err"
        fail=1
        continue
    fi

    # SchemaVersion guard, as in step 6: a reshaped field would make the jq
    # below report nothing rather than fail.
    schema_version=$(jq -r '.SchemaVersion' "$json")
    if [[ "$schema_version" != "2" ]]; then
        echo "ERROR: trivy JSON SchemaVersion is '$schema_version', this script expects 2"
        fail=1
        continue
    fi

    # trivy's IDs are "KSV-0041"; trivy-operator's, and so the accepted list's,
    # are "AVD-KSV-0041". sort -u because some checks report one finding per
    # offending rule or container, and the list decides per object.
    findings=$(jq -r '
        .Results[]? | (.Target | sub("\\.yaml$"; "")) as $t | .Misconfigurations[]?
        | select(.Status == "FAIL")
        | [.Severity, $t, ("AVD-" + .ID), .Title] | @tsv
    ' "$json" | sort -u | awk -f scripts/trivy-accepted-filter.awk scripts/trivy-accepted-findings.txt -)
    if [[ -n "$findings" ]]; then
        echo "$findings" | column -t -s "$(printf '\t')"
        fail=1
    fi

    # Objects trivy scanned. Zero means the split matched nothing — a changed
    # flate output format, say — which the harness fails.
    checked=$((checked + $(jq -r '.Results | length // 0' "$json")))
done

if [[ $fail -ne 0 ]]; then
    echo ""
    echo "Each finding is fixed, or accepted with its reason in"
    echo "scripts/trivy-accepted-findings.txt (docs/runbooks/trivy-operator-audit.md)."
fi
echo "CHECKED $checked objects"
exit $fail
