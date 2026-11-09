# A k3s Upgrade Pod Shows `Unknown`

**Symptom:** after a k3s release reaches a site, `kubectl get pods -n
system-upgrade` shows an `apply-server-plan-on-<node>-…` pod with status
`Unknown`. `kubectl describe` says the `upgrade` container ended with `Reason:
Unknown` and `Exit Code: 255`. A second pod from the same Job shows `Completed`.

This is usually expected, and needs no action. The upgrade pod runs on the node
it upgrades, and its last step is to kill the running k3s so the new binary
starts. The kubelet is part of k3s, so the likely reason is that the container
ended while no kubelet was running to record its exit, and it came back
`Unknown`. The Job counts that as a failure and starts a second pod, which finds
the binary already replaced and completes. It doesn't happen on every upgrade,
so a site that upgraded cleanly to the same release is no evidence against it.

The checks below confirm that this is what happened. If any of them fails, the
upgrade really did fail, and this runbook doesn't apply.

## Checks

**1. The failed pod got as far as killing k3s.** Its log ends with these
three lines:

```
[INFO]  Deploying new k3s binary to /usr/local/bin/k3s
[INFO]  K3s binary has been replaced successfully
[INFO]  Successfully killed old k3s pid <pid>
```

In Grafana (Loki), dropping the shell trace (lines starting `+ `):

```logql
{site="<site>", namespace="system-upgrade", container="upgrade"} != "+ "
```

Or at the site, while the pod still exists:

```bash
kubectl -n system-upgrade logs <unknown-pod> | grep INFO
```

**2. A later pod in the same Job completed, having found the binary
already replaced.** Its log reads `Binary already been replaced`, and the k3s
pid it reports is different from the one the first pod killed.

**3. The node runs the plan's version.** The `kubelet_version` label matches
the `version:` in `base/system-upgrade-controller/plans.yaml` on the branch the
site watches:

```promql
kube_node_info{site="<site>"}
```

Or `kubectl get nodes` at the site.

**4. Everything else came back.** Pods that k3s restarted show restarts at the
time of the upgrade (for example cert-manager, the Flux controllers,
`local-path-provisioner`). Nothing is left out of `Running` or `Completed`
apart from the `Unknown` pod:

```bash
kubectl get pods -A | grep -Ev 'Running|Completed'
```

## Resolution

None. The `Unknown` pod is removed when the Job is cleaned up. Deleting it by
hand is harmless if it gets in the way sooner:

```bash
kubectl -n system-upgrade delete pod <unknown-pod>
```

## Appendix: examples

**Eastbank, v1.37.1 → v1.37.2+k3s1, 2026-11-09.** The first pod killed k3s pid
794 at 08:09:56 UTC, and its container ended `Unknown`/255 at 08:10:49. The
second pod found k3s running as pid 650999, reported `Binary already been
replaced`, and completed. Akron took the same release without a failed pod.
