#!/usr/bin/env bash
# Filter a trivy-operator ConfigAuditReport dump down to the findings nobody has
# accepted yet. Run on the dev machine against a JSON dump taken from a cluster:
#
#   # on the cluster host — raw data only, no tooling needed there
#   kubectl get configauditreports -A -o json > akron-reports.json
#
#   # here, where the accepted list and this script are version-controlled
#   ./scripts/trivy-audit-diff.sh akron-reports.json
#
# Empty output means nothing has changed since the last audit. See
# docs/runbooks/trivy-operator-audit.md.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACCEPTED="${TRIVY_ACCEPTED:-$REPO_ROOT/docs/trivy-accepted-findings.txt}"

filter() {
  local reports=$1 accepted=$2
  jq -r '
    .items[] | .metadata.namespace as $ns | .metadata.name as $n | .report.checks[]
    | select(.success == false)
    | [.severity, ($ns + "/" + $n), .checkID, .title] | @tsv
  ' "$reports" |
    # ReplicaSet and pod hashes change on every rollout; the accepted list keys
    # on the stable part of the name.
    sed -E 's#(/[a-z-]+)-[a-f0-9]{5,10}\t#\1\t#' |
    awk -F'[ \t]+' '
      NR==FNR {
        sub(/#.*/, ""); if ($0 ~ /^[ \t]*$/) next
        # Three shapes: a bare check ID (accepted everywhere), a bare
        # "namespace/" (accepted wholly), or an exact workload/check pair.
        if (NF == 1) { if ($1 ~ /^AVD-/) chk[$1]; else pfx[$1] }
        else pair[$1 " " $2]
        next
      }
      ($3 in chk) { next }
      (($2 " " $3) in pair) { next }
      { for (p in pfx) if (index($2, p) == 1) next; print }
    ' "$accepted" - |
    sort
}

self_test() {
  local tmp; tmp=$(mktemp -d); trap 'rm -rf "$tmp"' RETURN

  cat > "$tmp/accepted.txt" <<'EOF'
# comments and blank lines are ignored

AVD-KSV-0125
kube-system/
dns/replicaset-pihole AVD-KSV-0003
EOF

  cat > "$tmp/reports.json" <<'EOF'
{"items":[
 {"metadata":{"namespace":"dns","name":"replicaset-pihole-74857c5686"},"report":{"checks":[
   {"checkID":"AVD-KSV-0003","severity":"LOW","title":"Default capabilities","success":false},
   {"checkID":"AVD-KSV-9999","severity":"HIGH","title":"Something brand new","success":false},
   {"checkID":"AVD-KSV-0125","severity":"MEDIUM","title":"Untrusted registry","success":false},
   {"checkID":"AVD-KSV-0001","severity":"LOW","title":"A passing check","success":true}]}},
 {"metadata":{"namespace":"kube-system","name":"replicaset-coredns-54996dc9b4"},"report":{"checks":[
   {"checkID":"AVD-KSV-0011","severity":"LOW","title":"CPU not limited","success":false}]}},
 {"metadata":{"namespace":"monitoring","name":"statefulset-loki"},"report":{"checks":[
   {"checkID":"AVD-KSV-0104","severity":"MEDIUM","title":"Seccomp policies disabled","success":false}]}}
]}
EOF

  # Accepted pair filtered; whole namespace filtered; global check filtered;
  # passing check never listed. A NEW check on an otherwise-accepted workload
  # must still appear — that is the property that stops this becoming a
  # blindfold.
  local expected="HIGH	dns/replicaset-pihole	AVD-KSV-9999	Something brand new
MEDIUM	monitoring/statefulset-loki	AVD-KSV-0104	Seccomp policies disabled"

  local actual; actual=$(filter "$tmp/reports.json" "$tmp/accepted.txt")
  if [[ "$actual" != "$expected" ]]; then
    echo "self-test FAILED" >&2
    diff <(echo "$expected") <(echo "$actual") >&2 || true
    return 1
  fi
  echo "self-test passed"
}

case "${1:-}" in
  --self-test) self_test ;;
  "" | -h | --help)
    echo "usage: $0 <configauditreports.json> | --self-test" >&2
    exit 1 ;;
  *) filter "$1" "$ACCEPTED" | column -t -s "$(printf '\t')" ;;
esac
