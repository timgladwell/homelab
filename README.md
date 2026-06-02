# Homelab

Single-node K3s homelab managed with Flux CD + Kustomize + Helm. See [CLAUDE.md](CLAUDE.md) for architecture details and development conventions.

## Host Prerequisites (Non-GitOps Steps)

Some cluster configuration must be applied directly on the host and is **not managed by Flux**. These steps are one-time setup requirements. Document any new host-level prerequisites here.

### K3s control-plane metrics exposure

By default, K3s binds `kube-controller-manager` and `kube-scheduler` metrics to `127.0.0.1`, making them unreachable from Prometheus. Apply the following to expose them on all interfaces.

Note: K3s uses SQLite as its datastore by default (not etcd), and does not run kube-proxy. Both are disabled in the Prometheus chart config — do not add `etcd-arg` or attempt to scrape kube-proxy.

**File:** `/etc/rancher/k3s/config.yaml` on the K3s host

This file does not exist by default — K3s only reads it if present. Create it with:

```yaml
kube-controller-manager-arg: "bind-address=0.0.0.0"
kube-scheduler-arg: "bind-address=0.0.0.0"
```

Before creating the file, check whether K3s was started with any existing flags so you don't accidentally drop them:

```bash
sudo systemctl cat k3s
```

After creating the file, restart K3s:

```bash
sudo systemctl restart k3s
```

This is a prerequisite for the `kube-controller-manager` and `kube-scheduler` Prometheus targets to show as `UP`.

### UniFi SIEM syslog forwarding

Two separate UniFi settings feed logs into Loki via Alloy. Configure both after the monitoring stack is deployed.

**General syslog** (`{job="unifi-siem"}`) — low-level system and controller events in RFC 3164 format:

In the UniFi controller UI: **Settings → System → Integrations** → set target to `syslog.${HOSTNAME}:1514`, protocol `UDP`, format `syslog`.

**CEF traffic/audit logs** (`{job="unifi-cef"}`) — network access, config changes, and cybersecure events in CEF format (no syslog PRI header):

In the UniFi controller UI: **Settings → Cybersecure → Traffic Logging** → set target to `syslog.${HOSTNAME}:1515`, protocol `UDP`.

Port 1515 uses an OTel syslog receiver with `allow_skip_pri_header = true` because UniFi's CEF export omits the `<PRI>` field required by RFC 3164.
