# kube-prometheus-stack

The chart runs at Akron only (`sites/akron/monitoring/kube-prometheus-stack.yaml`). Its major version tracks the bundled **prometheus-operator**, and an operator bump is what usually brings CRD changes.

## Upgrade checklist

For any kube-prometheus-stack chart version bump:

1. **Read the chart release notes** for every version in the range ([helm-charts releases](https://github.com/prometheus-community/helm-charts/releases?q=kube-prometheus-stack)). Most entries are image bumps. Cross-reference each values change against the HelmRelease. This repo disables most of the chart (kubelet, kubeApiServer, nodeExporter, kubeStateMetrics, coreDns and the control-plane monitors), so a change to one of those usually does not apply.

2. **Read the PR's `render-diff` comment.** It shows the operator image, the Prometheus, Alertmanager and Grafana images, RBAC and rule changes, with values applied. The operator image tag is the version the CRDs must match.

3. **If the operator version changed, read its release notes** ([prometheus-operator releases](https://github.com/prometheus-operator/prometheus-operator/releases)) for breaking changes and CRD changes, then apply the CRDs **before merging**:
   ```bash
   kubectl apply --server-side --force-conflicts \
     -f https://github.com/prometheus-operator/prometheus-operator/releases/download/v<OPERATOR_VERSION>/stripped-down-crds.yaml

   # Confirm. Prints the operator version the installed CRDs came from.
   kubectl get crd prometheuses.monitoring.coreos.com \
     -o jsonpath='{.metadata.annotations.operator\.prometheus\.io/version}'
   ```
   Applying the CRDs restarts kube-state-metrics and loads the API server. See [What a CRD apply does to the cluster](helm-crd-upgrades.md#what-a-crd-apply-does-to-the-cluster). **Wait for that to settle before merging**, so the CRD apply and the chart rollout show up separately on the dashboards.

4. **If Prometheus's own version changed, read its release notes** ([prometheus releases](https://github.com/prometheus/prometheus/releases)). A deprecated command-line flag is usually not a concern: the operator passes flags only when they differ from the defaults. For example, it omits `--log.level` when `logLevel` is `info` (`pkg/prometheus/promcfg.go`).

## What the rollout looks like

After merging, an operator bump restarts Prometheus **twice**. The Helm upgrade restarts it once, and the new operator restarts it again when it reconciles the `Prometheus` object with its own config-reloader image. Alertmanager does the same.

Each start replays the WAL, which takes about 30s. Until Prometheus logs `Server is ready to receive web requests`, every Grafana query against it returns **502**, `up` included. Loki keeps working, so read the Prometheus pod's logs there:

```logql
{namespace="monitoring", pod="prometheus-kube-prometheus-stack-prometheus-0"}
```

The HelmRelease shows `Unknown` / `Running 'upgrade' action` for the whole rollout, up to its 5m timeout. Then check:

```bash
flux get hr -n monitoring kube-prometheus-stack
kubectl -n monitoring get sts,pods
kubectl -n monitoring logs deploy/kube-prometheus-stack-operator | grep -i forbidden
```

The last command catches RBAC denials. Operator releases have been tightening the operator's ClusterRole, which v0.94 did by replacing wildcard verbs with explicit ones.
