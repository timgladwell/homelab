---
name: trivy-audit-conventions
description: How to triage findings from the periodic trivy-operator cluster audit, and how to record the decisions. Use whenever reading ConfigAuditReport output, deciding whether a finding is fixed or accepted, or editing docs/trivy-accepted-findings.txt.
---

# trivy audit conventions

`trivy-operator` is shipped for the length of an audit and removed again — it is
not a permanent workload. The full cycle is in
`docs/runbooks/trivy-operator-audit.md`; this covers the judgement.

## Getting the findings

The cluster produces raw data; the judgement happens in the repo.

```bash
# on the cluster host, by the user — Claude never touches a cluster
kubectl get configauditreports -A -o json > <site>-reports.json

# here
./scripts/trivy-audit-diff.sh <site>-reports.json
```

Output is only what nobody has accepted yet. **Empty output is the expected
result most cycles** — report that plainly rather than hunting for something to
say.

Never re-derive the whole report by reading the JSON directly unless the diff
script's output is genuinely insufficient. The filtering is the point.

## Classifying what survives the filter

Every remaining finding is exactly one of three things. The distinction between
the last two is the one that matters.

**Fix** — a supported chart value or manifest field changes. Most findings are
this. Chart values are opaque to the validation pipeline, so a fix must be
verified with `flate build hr -p clusters/<site>` and the rendered securityContext
read back, never assumed from the values block.

**Accept** — we looked, and the answer is no, permanently. Goes in
`docs/trivy-accepted-findings.txt` with the reason. Legitimate grounds:

- the behaviour is the workload's entire function (node-exporter's `hostPID`,
  metallb-speaker's `NET_RAW`)
- another party owns the lifecycle (k3s's `kube-system`, Flux's bootstrap
  manifests, which k3s or the Flux CLI rewrite on upgrade)
- the check's premise does not apply here, argued explicitly
- fixing it costs more than the risk (a recursive `chown` of a 14Gi TSDB on a Pi
  to satisfy a LOW about UID ranges)

**Defer** — worth doing, not now. **Goes in an issue, never in the accepted
file.** A file whose job is to stop things being looked at is the worst possible
place to park work someone intends to do. If it is in the accepted file it will
never be seen again, so putting undecided work there is not filing it — it is
losing it.

When in doubt between accept and defer, defer. A finding that keeps reappearing
is a mild annoyance; one silently filtered forever is a hole.

## Editing the accepted file

Three entry shapes, matched by `scripts/trivy-audit-diff.sh`:

| Shape | Meaning |
|---|---|
| `AVD-KSV-0125` | that check, accepted everywhere |
| `kube-system/` | everything under that namespace |
| `dns/replicaset-pihole AVD-KSV-0003` | one exact workload/check pair |

Prefer the narrowest shape that expresses the actual decision. A bare check ID
is right for something like the untrusted-registry check, which carries no
information anywhere; it is wrong as a shortcut for "this fires in a few places".

Workload names have their ReplicaSet/pod hash stripped before matching, so write
`replicaset-pihole`, not `replicaset-pihole-74857c5686`.

**Every entry carries its reason** as a `#` comment above its group — the same
standard as `.trivyignore.yaml`, whose reasoning should be mirrored rather than
reinvented where the two overlap. An entry without a reason is indistinguishable
from an oversight six months later.

Run `./scripts/trivy-audit-diff.sh --self-test` after touching the script.

## Reporting format

```
## <site> audit — <n> findings, <m> after filtering

| Severity | Workload | Check | Proposed |
|---|---|---|---|
| HIGH | monitoring/replicaset-grafana | AVD-KSV-0014 | fix — chart value `grafana.containerSecurityContext` |
| LOW | monitoring/daemonset-alloy | AVD-KSV-0030 | defer — needs testing against its hostPath mounts |
```

State the proposal and its one-line grounds. Do not edit
`docs/trivy-accepted-findings.txt` as part of reporting — accepting a finding is
a decision the user makes, in a context they can see.

## What this audit cannot see

Containers the *operator* injects at pod-creation time — the prometheus-operator's
`config-reloader` and `init-config-reloader` — are configured by controller CLI
flags. They are in no manifest, no rendered chart, and if a `ResourceQuota`
rejects the pod, no pod exists to scan either. That took Prometheus down for five
hours unnoticed (#266). Alongside the reports, check for zero-ready workloads:

```bash
kubectl get statefulset,deploy -A | awk '$3 ~ /^0\// || $2 ~ /^0\//'
```

Proper coverage of this is alerting's job (#214), not this tool's.
