# Drop findings recorded in scripts/trivy-accepted-findings.txt.
#
#   awk -f trivy-accepted-filter.awk <accepted-list> <findings.tsv>
#
# Findings are "<severity>\t<object>\t<AVD-KSV-NNNN>\t<title>", where <object>
# is "<namespace>/<kind>-<name>", or "<kind>-<name>" for a cluster-scoped
# object. Shared by the two callers that judge findings against the same
# decisions: scripts/trivy-audit-diff.sh (live objects, from trivy-operator) and
# validation step 16 (the same objects as rendered from git).
BEGIN { FS = "[ \t]+" }
NR == FNR {
    sub(/#.*/, ""); if ($0 ~ /^[ \t]*$/) next
    # Three shapes: a bare check ID (accepted everywhere), a bare
    # "namespace/" (accepted wholly), or an exact object/check pair.
    if (NF == 1) { if ($1 ~ /^AVD-/) chk[$1]; else pfx[$1] }
    else pair[$1 " " $2]
    next
}
($3 in chk) { next }
(($2 " " $3) in pair) { next }
{ for (p in pfx) if (index($2, p) == 1) next; print }
