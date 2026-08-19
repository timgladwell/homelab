# Periodic Cluster Security Audit with trivy-operator

Validation step 6 scans the YAML in git. It cannot see anything a Helm chart
renders on the cluster — Prometheus, Grafana, Loki, Alertmanager, Traefik, both
Alloys, kube-state-metrics — and it has no cross-object correlation, so
namespace-scoped checks like `KSV-0040` are unsatisfiable against a bag of files
(#242).

`trivy-operator` closes both gaps by scanning live objects. It is **not left
running**: it is shipped for the length of an audit, its findings are read and
dealt with, and then it is removed. A controller watching every object on an 8GB
Pi is not worth paying for between audits, and a permanent dashboard over
findings nobody looks at is worse than none.

Run this every few months, and after any change that adds a Helm release.

## 1. Ship it

Restore `base/trivy-operator/` and the line that opts each site in. All of it is
in git history — find the last audit's removal commit and revert it:

```bash
git log --oneline --diff-filter=D -- base/trivy-operator/trivy-operator.yaml
git checkout <that-commit>^ -- base/trivy-operator
```

Then add to the `monitoring` kustomization of each site being audited:

```yaml
  - ../../../base/trivy-operator
```

Then a normal PR to `main`. Akron reconciles it straight away; **Eastbank only
sees it after a promotion to `stable`**, so auditing Eastbank costs two
promotions — one to ship, one to unship. Fold them into promotions you are doing
anyway rather than promoting twice for the audit alone.

Wait for reconciliation:

```bash
flux reconcile kustomization monitoring --with-source
kubectl -n trivy-system rollout status deploy/trivy-operator
```

First run downloads the trivy-checks policy bundle and scans every workload;
give it a few minutes.

**Audit each site at least once.** Most findings are in `base/`, so a fix at
Akron fixes both — but not all of them. `Unpoller` runs only at Eastbank, and
`Prometheus`, `Grafana`, `Loki` and the logs `alloy` DaemonSet only at Akron.
Anything reached only through a chart's rendered output exists nowhere else.

## 2. Read the findings

```bash
# Everything that failed, worst first
kubectl get configauditreports -A -o json | jq -r '
  .items[] | .metadata.namespace as $ns | .report.checks[]
  | select(.success == false)
  | [.severity, $ns, .checkID, .title] | @tsv' | sort | column -t -s $'\t'

# Which workloads carry the most
kubectl get configauditreports -A \
  -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name,CRIT:.report.summary.criticalCount,HIGH:.report.summary.highCount,MED:.report.summary.mediumCount,LOW:.report.summary.lowCount

# RBAC, same shape
kubectl get rbacassessmentreports,clusterrbacassessmentreports -A
```

The interesting column is `checkID`. Cross-reference it against
`.trivyignore.yaml`: a check that fails here and is ignored there means the
ignore is wrong or stale.

## 3. Deal with them

Each finding is one of three things:

- **A real fix** — the manifest or Helm values change. Normal PR.
- **A chart default we cannot reach** — record it in `.trivyignore.yaml` with
  the reason, same as any other exception. Findings against chart-rendered
  objects have no file path, so note the release name in the statement.
- **A check that does not apply here** — exception with a reason, never a
  severity floor.

**What this audit still cannot see.** A container the *operator* injects at
pod-creation time — the prometheus-operator's `config-reloader` and
`init-config-reloader` are the known case — is configured by controller CLI
flags, not by anything in git or in a rendered chart. If such a pod is rejected
(a `ResourceQuota` refusing a container that declares no limits) it is never
created, so there is nothing for the operator to scan and the workload simply
shows 0/1 with no pod object. Check `kubectl get statefulset,deploy -A` for
zero-ready workloads alongside the reports; that is the gap alerting covers
(#214), not this.

`KSV-0040` specifically: check whether the operator resolves it per-namespace
the way the check's name implies. If it passes on workloads in namespaces that
have a `ResourceQuota` and fails only where one is missing, that is the answer
#242 wanted, and the repo-wide `AVD-KSV-0040` exception in `.trivyignore.yaml`
should narrow to the namespaces that deliberately have no quota
(`system-upgrade`, `kube-system`, `flux-system`).

## 4. Unship it

Once the findings are closed out, delete `base/trivy-operator/` and every
`kustomization.yaml` line pointing at it, in a PR. Flux's `prune: true` removes
the Deployment, the namespace and the reports with it. Remember Eastbank needs
the promotion to actually lose it.

The chart's CRDs are **not** pruned — Flux does not remove CRDs installed by a
Helm chart on release deletion, and that is fine: they are inert without the
operator and save the next audit a re-install. Remove them by hand only if
something else needs the API group free:

```bash
kubectl get crd -o name | grep aquasecurity.github.io
```

Record the date and the headline result in the PR description, so the next audit
knows what changed since.
