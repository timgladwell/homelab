# Homelab

Single-node K3s homelab managed with Flux CD + Kustomize + Helm. See [CLAUDE.md](CLAUDE.md) for architecture details and development conventions.

## Host Prerequisites (Non-GitOps Steps)

Some cluster configuration must be applied directly on the host and is **not managed by Flux**. These steps are one-time setup requirements. Document any new host-level prerequisites here.

### K3s control-plane metrics exposure

By default, K3s binds `kube-controller-manager`, `kube-scheduler`, and `etcd` metrics to `127.0.0.1`, making them unreachable from Prometheus. Apply the following to expose them on all interfaces:

**File:** `/etc/rancher/k3s/config.yaml` on the K3s host

This file does not exist by default — K3s only reads it if present. Create it with:

```yaml
kube-controller-manager-arg: "bind-address=0.0.0.0"
kube-scheduler-arg: "bind-address=0.0.0.0"
etcd-arg: "listen-metrics-urls=http://0.0.0.0:2381"
```

Before creating the file, check whether K3s was started with any existing flags so you don't accidentally drop them:

```bash
sudo systemctl cat k3s
```

After creating the file, restart K3s:

```bash
sudo systemctl restart k3s
```

This is a prerequisite for the `kube-controller-manager`, `kube-scheduler`, and `kube-etcd` ServiceMonitors in `infrastructure/homelab/monitoring/k3s-controlplane-servicemonitors.yaml` to return data.

### UniFi SIEM syslog forwarding

Once Promtail is deployed (Phase 6), configure the UDM to forward syslog events to Promtail:

In the UDM controller UI: **Settings → System → Remote Logging** → set target to `<node-ip>:1514`, protocol `UDP`, format `syslog`.

This is a prerequisite for the `{job="unifi-siem"}` log stream in Loki/Grafana Explore.
