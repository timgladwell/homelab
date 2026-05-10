# Monitoring Stack — Implementation Task List

## Overview

Add metrics collection, log aggregation, and Grafana dashboards to the homelab.
The stack is **Prometheus + Loki + Grafana + OTel Collector** (PLG+OTel — the practical
LGTM subset for a small cluster. Mimir/Thanos for HA metrics storage and Tempo for
distributed tracing are natural Phase 11/12 follow-ons; skipped here to keep the initial
footprint small, not as a permanent architectural decision).

All work follows the existing GitOps pattern: Flux CD + Kustomize + HelmReleases,
SOPS-encrypted secrets, subdomain routing via Traefik.

**Confirmed infrastructure (already deployed on `main`):**
- MetalLB v0.15 in L2/ARP mode — provides stable external IPs for LoadBalancer services
- Traefik exposed via MetalLB at `${METALLB_TRAEFIK_IP}` (10.6.1.80); no `hostNetwork`
- PiHole DNS port 53 exposed via MetalLB at `${METALLB_PIHOLE_IP}` (10.6.1.53) on service `pihole-dns`; no `hostNetwork`
- New `cluster-vars.yaml` entries: `METALLB_ADDRESS_RANGE`, `METALLB_TRAEFIK_IP`, `METALLB_PIHOLE_IP`
- System upgrade controller for automated K3s updates

### Target coverage

| Source | Metrics | Logs |
|--------|---------|------|
| K3s system (apiserver, scheduler, controller-manager, etcd) | ✓ | ✓ |
| Homelab host (CPU, memory, disk, network, temperature) | ✓ | ✓ |
| PiHole | ✓ | ✓ |
| Unbound | ✓ | ✓ |
| UniFi network (UDM) | ✓ | ✓ (SIEM syslog) |
| Traefik ingress | ✓ | ✓ |
| Flux CD | ✓ | ✓ |

### Sizing constraints

| Component | PVC | Retention |
|-----------|-----|-----------|
| Prometheus | 14 Gi | ~~14 days / 12 GB~~ → **7 days / 6 GB** (tuned PR #50) |
| Loki | 8 Gi | 7 days |
| Grafana | 2 Gi | — |
| OTel Collector | — (no persistent store) | — |
| **Total** | **~24 Gi** | within 20–30 GB budget |

### Key conventions

- Namespace: `monitoring`
- New files land in `infrastructure/homelab/monitoring/`
- Monitoring is wired into the existing `infrastructure` Flux Kustomization
  (same pattern as `dns/` and `traefik/`; no new Flux Kustomization object needed)
- All secrets follow the `*secret.sops.yaml` naming convention and are SOPS-encrypted
- ARM64 compatibility required for all images (Raspberry Pi 4B)

---

## Phase 1 — Foundation ✅ COMPLETE

### Task 1.1 — Create monitoring namespace and directory skeleton ✅

**Files to create:**
```
infrastructure/homelab/monitoring/namespace.yaml
infrastructure/homelab/monitoring/kustomization.yaml
```

`namespace.yaml` — standard `v1/Namespace` named `monitoring`.

`kustomization.yaml` — start with an empty resources list; each subsequent task
adds entries to it.

**File to modify:**
```
infrastructure/homelab/kustomization.yaml
```
Add `- ./monitoring` to the resources list.

**Verify:** `kustomize build infrastructure/homelab/` renders the Namespace without errors.

---

### Task 1.2 — Add HelmRepositories ✅

**Files to create:**
```
infrastructure/homelab/monitoring/prometheus-helmrepo.yaml
infrastructure/homelab/monitoring/grafana-helmrepo.yaml
```

`prometheus-helmrepo.yaml`
- `HelmRepository` name: `prometheus-community`
- URL: `https://prometheus-community.github.io/helm-charts`
- interval: `24h`
- namespace: `monitoring`

`grafana-helmrepo.yaml`
- `HelmRepository` name: `grafana`
- URL: `https://grafana.github.io/helm-charts`
- interval: `24h`
- namespace: `monitoring`

Add both to `kustomization.yaml`.

---

## Phase 2 — Metrics Stack (Prometheus + Grafana + Alertmanager) ✅ COMPLETE

### Task 2.1 — Deploy kube-prometheus-stack ✅

**Files to create:**
```
infrastructure/homelab/monitoring/kube-prometheus-stack.yaml
```

`HelmRelease` targeting chart `kube-prometheus-stack` from the
`prometheus-community` HelmRepository. Pinned to `~83.x`.

Key Helm values:

```yaml
grafana:
  enabled: true
  adminPassword: ""           # overridden by secret — see Task 2.2
  persistence:
    enabled: true
    size: 2Gi
  sidecar:
    dashboards:
      enabled: true
      searchNamespace: ALL    # auto-loads ConfigMaps labelled grafana_dashboard: "1"
    datasources:
      enabled: true
  resources:
    requests: { cpu: 100m, memory: 128Mi }
    limits:   { cpu: 300m, memory: 256Mi }

prometheus:
  prometheusSpec:
    retention: 7d          # reduced from 14d in PR #50
    retentionSize: 6GB     # reduced from 12GB in PR #50
    storageSpec:
      volumeClaimTemplate:
        spec:
          accessModes: [ReadWriteOnce]
          resources:
            requests:
              storage: 14Gi
    serviceMonitorSelectorNilUsesHelmValues: false   # scrape ALL ServiceMonitors
    ruleSelectorNilUsesHelmValues: false              # pick up ALL PrometheusRules
    resources:
      requests: { cpu: 200m, memory: 512Mi }
      limits:   { cpu: 500m, memory: 2Gi }   # raised from 1Gi in PR #52

alertmanager:
  alertmanagerSpec:
    resources:
      requests: { cpu: 50m, memory: 64Mi }
      limits:   { cpu: 100m, memory: 128Mi }

nodeExporter:
  enabled: true       # host CPU, memory, disk, network, temperature

kubeStateMetrics:
  enabled: true       # K8s object metrics

prometheusOperator:
  resources:
    requests: { cpu: 100m, memory: 128Mi }
    limits:   { cpu: 200m, memory: 256Mi }
```

All kube-prometheus-stack images are multi-arch (ARM64 ✓).

Add to `kustomization.yaml`.

---

### Task 2.2 — Grafana admin secret ✅

**File to create:**
```
infrastructure/homelab/monitoring/grafana-secret.sops.yaml
```

Kubernetes `Secret` in namespace `monitoring`, name `grafana-secret`.
`stringData.admin-password` → SOPS-encrypted value.

Reference in the HelmRelease:
```yaml
grafana:
  admin:
    existingSecret: grafana-secret
    passwordKey: admin-password
```

Use `scripts/secrets-helper.sh` to encrypt.

---

### Task 2.3 — Grafana IngressRoute ✅

**File to create:**
```
infrastructure/homelab/monitoring/grafana-ingressroute.yaml
```

Traefik `IngressRoute` routing `grafana.${DOMAIN}` →
`kube-prometheus-stack-grafana` service on port 80 in namespace `monitoring`.
Follow the pattern in `infrastructure/homelab/dns/pihole-ingressroute.yaml`.

Traefik is already running as a MetalLB `LoadBalancer` at `${METALLB_TRAEFIK_IP}`.
The IngressRoute approach is unchanged — this is purely a backend service type detail
that does not affect how IngressRoutes are authored.

---

### Task 2.4 — K3s control-plane scraping ✅ (partially reverted — see PR #51)

K3s embeds controller-manager and scheduler in its own process (rather than
running them as pods), and uses SQLite instead of etcd. This means:
- `kubeEtcd` is disabled — K3s has no etcd
- `kubeProxy` is disabled — K3s uses its own routing, not kube-proxy
- Controller-manager and scheduler must be scraped via the **node IP**, not a
  pod selector, because they are not Kubernetes pods

**Host prerequisite (outside GitOps, done once):**
Edit `/etc/rancher/k3s/config.yaml` on the host:
```yaml
kube-controller-manager-arg: "bind-address=0.0.0.0"
kube-scheduler-arg: "bind-address=0.0.0.0"
```
Then restart K3s. This exposes metrics on the node's network interface.
No `etcd-arg` is needed — K3s uses SQLite.

**GitOps implementation — chart-native endpoints:**
The `kube-prometheus-stack` chart has built-in support for this K3s pattern.
Supply the node IP via `${NODE_IP}` and the chart creates headless Services and
Endpoints automatically, along with its own ServiceMonitors. No custom
ServiceMonitor files are needed.

`NODE_IP: "10.6.1.3"` is defined in `clusters/homelab/cluster-vars.yaml`.

Relevant section of `kube-prometheus-stack.yaml`:
```yaml
kubeControllerManager:
  endpoints:
    - ${NODE_IP}
  service:
    port: 10257
    targetPort: 10257
  serviceMonitor:
    https: true
    insecureSkipVerify: true

kubeScheduler:
  endpoints:
    - ${NODE_IP}
  service:
    port: 10259
    targetPort: 10259
  serviceMonitor:
    https: true
    insecureSkipVerify: true

kubeEtcd:
  enabled: false

kubeProxy:
  enabled: false
```

> **PR #51 follow-up:** `kubeControllerManager` and `kubeScheduler` scrapers were
> subsequently **disabled** on the deployed cluster. On K3s these components are
> embedded in the k3s server binary and expose the full process metric set (~51K
> samples/scrape); after relabeling only ~724 series survive — not worth the
> overhead on a throttling RPi. The host prerequisite (bind-address config) was
> left in place but the chart sections are now `enabled: false` in the deployed
> `kube-prometheus-stack.yaml`.

---

### Task 2.5 — Node Exporter Full dashboard ✅ (done early — PR #49)

Deployed ahead of Phase 9 to help diagnose RPi thermal throttling (CPU reaching
81 °C with Prometheus compaction as the primary I/O driver).

**Files created:**
```
infrastructure/homelab/monitoring/dashboards/kustomization.yaml   ← bootstrapped Phase 9.1
infrastructure/homelab/monitoring/dashboards/node-exporter-full.json
```

Grafana dashboard **1860** (Node Exporter Full) — surfaces CPU temperature,
throttling state, clock frequency, CPU/memory/disk/network for the RPi host via
the already-deployed `node-exporter`.

Used `configMapGenerator` (JSON file on disk) rather than embedding 468 KB of
JSON inline in YAML. The `dashboards/kustomization.yaml` bootstrapped for this
task is the same file Phase 9.1 would have created — Phase 9.1 only needs to
add remaining dashboard entries.

---

### Task 2.6 — Prometheus operational tuning ✅ (PRs #50, #51, #52)

Emergency tuning applied after initial deployment revealed the RPi was thermal
throttling at 81 °C with sustained 250 MB/s SSD reads driven by Prometheus
compaction.

**Root cause (PR #52):** Memory was capped at 1 Gi. With 209K active series
Prometheus hit the limit every 2–3 minutes, triggering premature head block
flushes. Each flush produced a small Level 1 block; the compactor then merged
them continuously, causing the I/O spike.

| Change | PR | Before | After |
|--------|----|--------|-------|
| Scrape interval | #50 | 15s | 60s |
| Evaluation interval | #50 | 15s | 60s |
| Retention | #50 | 14d / 12 GB | 7d / 6 GB |
| Prometheus memory limit | #52 | 1 Gi | 2 Gi |
| kubeControllerManager scraper | #51 | enabled | disabled |
| kubeScheduler scraper | #51 | enabled | disabled |

Raising memory to 2 Gi restores the normal 2 h head compaction cycle (~40× less
frequent than before), which should bring I/O back to baseline.

---

### Task 2.7 — Fix: enable PodMonitor discovery in kube-prometheus-stack ✅

**Fixup for Task 2.1.** The deployed `kube-prometheus-stack.yaml` sets
`serviceMonitorSelectorNilUsesHelmValues: false` and `ruleSelectorNilUsesHelmValues: false`
but is missing the equivalent for PodMonitors. Without it, Prometheus applies the chart's
own Helm label selector to PodMonitor discovery, silently ignoring any PodMonitor not
bearing the chart's release label — including the unpoller PodMonitor added in Task 6.2.

**File to modify:**
```
infrastructure/homelab/monitoring/kube-prometheus-stack.yaml
```

Add to `prometheus.prometheusSpec`:
```yaml
podMonitorSelectorNilUsesHelmValues: false   # scrape ALL PodMonitors (e.g. unpoller)
```

---

### Task 2.8 — Fix: human-readable instance labels on node-exporter metrics ✅

**Fixup for Task 2.1.** By default, node-exporter metrics carry `instance="<node-ip>:9100"`.
In Grafana, dashboard variables that use `instance` as a label show raw IP:port values
(e.g. `10.6.1.3:9100`) rather than hostnames. On a multi-node cluster this is unreadable.

**File to modify:**
```
infrastructure/homelab/monitoring/kube-prometheus-stack.yaml
```

Add a relabeling rule to the `nodeExporter` serviceMonitor to replace the `instance`
label with the Kubernetes node name:
```yaml
nodeExporter:
  enabled: true
  serviceMonitor:
    relabelings:
      - sourceLabels: [__meta_kubernetes_node_name]
        targetLabel: instance
```

This makes all node-exporter dashboards (Node Exporter Full, etc.) display node names
in their host/instance dropdowns instead of IP addresses.

> **Other exporters:** PiHole, Unbound, and unpoller ServiceMonitors/PodMonitors should
> include equivalent relabeling when they are created (Tasks 4.1, 5.1, 6.2). Apply the
> same pattern: map a human-readable identifier (pod name, node name, or service name)
> onto the `instance` label in each exporter's ServiceMonitor/PodMonitor spec.

---

## Phase 3 — Log Aggregation (Loki + Grafana Alloy) ✅ COMPLETE

### Task 3.1 — Deploy Loki (single-binary) ✅

**File to create:**
```
infrastructure/homelab/monitoring/loki.yaml
```

`HelmRelease` targeting chart `loki` from the `grafana` HelmRepository.
Use `SingleBinary` deployment mode (appropriate for small clusters; scale to
`SimpleScalable` with 3 replicas or full microservices mode as log volume grows).

Key values:
```yaml
loki:
  commonConfig:
    replication_factor: 1
  storage:
    type: filesystem
  compactor:
    retention_enabled: true
    retention_delete_delay: 2h
    delete_request_store: filesystem   # required when retention_enabled: true
  limitsConfig:                        # camelCase — snake_case is silently ignored by chart v6
    retention_period: 168h
  schemaConfig:                        # camelCase — snake_case is silently ignored by chart v6
    configs:
      - from: "2024-01-01"
        store: tsdb
        object_store: filesystem
        schema: v13
        index:
          prefix: loki_index_
          period: 24h

deploymentMode: SingleBinary

singleBinary:
  replicas: 1
  persistence:
    enabled: true
    size: 8Gi
  resources:
    requests: { cpu: 100m, memory: 128Mi }
    limits:   { cpu: 300m, memory: 512Mi }

chunksCache:
  enabled: false    # memcached — unnecessary overhead in SingleBinary mode; enable when scaling to SimpleScalable

resultsCache:
  enabled: false    # memcached — same reason; re-evaluate when query volume justifies the cost

backend:
  replicas: 0
read:
  replicas: 0
write:
  replicas: 0
```

> **Loki chart v6 gotchas (learned during deployment):**
> - `schemaConfig` and `limitsConfig` must be **camelCase** — the chart's `validate.yaml` template
>   reads these keys directly and silently ignores the snake_case equivalents, causing a startup failure.
> - `compactor.delete_request_store` must be set when `retention_enabled: true` — omitting it
>   causes Loki to exit immediately with a config error.
> - The chart deploys `memcached` StatefulSets for `chunksCache` and `resultsCache` by default.
>   On the RPi these fail to schedule (Insufficient memory). Disable both explicitly.

Loki images are multi-arch (ARM64 ✓).

---

### Task 3.2 — Deploy Grafana Alloy ✅

> **Note:** Promtail reached end-of-life and has been superseded by
> [Grafana Alloy](https://grafana.com/docs/alloy/latest/). Alloy is a
> unified collector for logs, metrics, and traces; it uses a River
> (HCL-like) configuration language rather than YAML snippets.

**File to create:**
```
infrastructure/homelab/monitoring/alloy.yaml
```

`HelmRelease` targeting chart `alloy` from the `grafana` HelmRepository.
Runs as a DaemonSet; collects pod logs via the Kubernetes API and journal
logs via the systemd journal.

Key Helm values:
```yaml
controller:
  type: daemonset

mounts:
  varlog: true
  extra:
    - name: journal
      mountPath: /var/log/journal
      readOnly: true
    - name: machine-id
      mountPath: /etc/machine-id
      readOnly: true

extraVolumes:
  - name: journal
    hostPath:
      path: /var/log/journal
      type: Directory
  - name: machine-id
    hostPath:
      path: /etc/machine-id
      type: File

resources:
  requests: { cpu: 50m, memory: 64Mi }
  limits:   { cpu: 100m, memory: 128Mi }

alloy:
  configMap:
    create: true
    content: |
      loki.write "default" {
        endpoint {
          url = "http://loki:3100/loki/api/v1/push"
        }
      }

      discovery.kubernetes "pods" {
        role = "pod"
      }

      discovery.relabel "pod_logs" {
        targets = discovery.kubernetes.pods.targets
        rule {
          source_labels = ["__meta_kubernetes_namespace"]
          target_label  = "namespace"
        }
        rule {
          source_labels = ["__meta_kubernetes_pod_name"]
          target_label  = "pod"
        }
        rule {
          source_labels = ["__meta_kubernetes_pod_container_name"]
          target_label  = "container"
        }
      }

      loki.source.kubernetes "pods" {
        targets    = discovery.relabel.pod_logs.output
        forward_to = [loki.write.default.receiver]
      }

      discovery.relabel "journal" {
        targets = []
        rule {
          source_labels = ["__journal__systemd_unit"]
          target_label  = "unit"
        }
        rule {
          source_labels = ["__journal__hostname"]
          target_label  = "host"
        }
      }

      loki.source.journal "system" {
        path          = "/var/log/journal"
        forward_to    = [loki.write.default.receiver]
        relabel_rules = discovery.relabel.journal.rules
        labels = {
          job = "systemd-journal",
        }
      }
```

Alloy images are multi-arch (ARM64 ✓).

---

### Task 3.3 — Add Loki datasource to Grafana ✅

Add to the kube-prometheus-stack HelmRelease values:
```yaml
grafana:
  additionalDataSources:
    - name: Loki
      type: loki
      url: http://loki:3100
      access: proxy
      isDefault: false
```

---

## Phase 4 — PiHole Metrics ✅ COMPLETE

### Task 4.1 — Deploy pihole-exporter ✅

> **Exporter selection note:** The originally planned `ekofr/pihole-exporter` is
> unmaintained (no commits since mid-2025) and is broken against PiHole v6, which
> shipped in February 2025 with a completely rewritten REST API and session-based
> authentication (no static API keys). Use **`bazmonk/pihole6_exporter`** instead,
> published as a multi-arch Docker image at `amonacoos/pihole6_exporter` on Docker Hub
> (`amonacoos` is the Docker Hub identity of `peatonet`, the author of
> [`peatonet/pihole6-exporter-docker`](https://github.com/peatonet/pihole6-exporter-docker),
> the dedicated Docker packaging repo for this exporter).
>
> **PiHole v6 auth prerequisite (manual step):**
> In the PiHole UI go to Settings → API → "App password" and generate a password.
> Store it in a SOPS-encrypted secret (see below) — this replaces the old static API token.

**Files to create:**
```
infrastructure/homelab/monitoring/pihole-exporter.yaml
infrastructure/homelab/monitoring/pihole-exporter-secret.sops.yaml
```

`pihole-exporter-secret.sops.yaml` — `Secret` in namespace `monitoring`,
SOPS-encrypted field `PIHOLE_API_KEY` (the app password from the PiHole UI).

`pihole-exporter.yaml` contains three resources in namespace `monitoring`:

1. `Deployment` — image `amonacoos/pihole6_exporter:latest` (ARM64 ✓)
   - Env (plain): `PIHOLE_HOST=pihole.dns.svc.cluster.local`, `PIHOLE_PORT=8080`,
     `PIHOLE_SCHEME=http`
   - Env (from secret): `PIHOLE_API_KEY` from `pihole-exporter-secret`
   - Resources: `requests: {cpu: 20m, memory: 32Mi}`, `limits: {cpu: 50m, memory: 64Mi}`

2. `Service` — port 9666

3. `ServiceMonitor` — targets the Service above on port 9666, scrape interval 30s,
   namespace `monitoring`

---

### Task 4.2 — PiHole Grafana dashboard ✅

**File to create:**
```
infrastructure/homelab/monitoring/dashboards/pihole-dashboard.yaml
```

`ConfigMap` in namespace `monitoring` with label `grafana_dashboard: "1"`.

Fetch dashboard JSON from Grafana.com ID **21043** ("Pi-hole ver6 stats") — this
dashboard is built specifically for `bazmonk/pihole6_exporter` so metric names
match exactly. ID 10176 (the old `ekofr` dashboard) is incompatible.

---

## Phase 5 — Unbound Metrics ✅ COMPLETE

### Task 5.1 — Enable Unbound statistics and add exporter sidecar ✅

**File to modify:**
```
infrastructure/homelab/dns/unbound-configmap.yaml
```

Add to the `server:` block:
```
statistics-interval: 0
extended-statistics: yes
statistics-cumulative: no
```

Add `remote-control:` block:
```
remote-control:
  control-enable: yes
  control-use-cert: no
  control-interface: 127.0.0.1
  control-port: 8953
```

**File to modify:**
```
infrastructure/homelab/dns/unbound-deployment.yaml
```

Add sidecar container using `ar51an/unbound-exporter` (verify ARM64 tag before
pinning). The sidecar shares the pod's loopback interface so it can reach
`127.0.0.1:8953` without network policies.

Add a second named port `metrics` (9167) to the existing `unbound` Service
(or create a separate headless Service in the `dns` namespace for scraping).

**File to create:**
```
infrastructure/homelab/monitoring/unbound-servicemonitor.yaml
```

`ServiceMonitor` targeting the `metrics` port on the Unbound service in the
`dns` namespace.

---

### Task 5.2 — Unbound Grafana dashboard ✅

**File to create:**
```
infrastructure/homelab/monitoring/dashboards/unbound-dashboard.yaml
```

`ConfigMap` with label `grafana_dashboard: "1"`.
Grafana.com dashboard ID **11705**.

---

### Task 5.3 — Fix: human-readable instance label on Unbound ServiceMonitor ✅

**Fixup for Task 5.1.** The deployed `unbound-servicemonitor.yaml` has no relabeling
rules, so Prometheus records Unbound metrics with `instance="<pod-ip>:9167"`. Follow
the pattern from Task 2.8.

**File to modify:**
```
infrastructure/homelab/monitoring/unbound-servicemonitor.yaml
```

Add to the `endpoints` entry:
```yaml
relabelings:
  - replacement: unbound
    targetLabel: instance
```

---

### Task 4.3 — Fix: human-readable instance label on PiHole ServiceMonitor ✅

**Fixup for Task 4.1.** The deployed `pihole-exporter.yaml` ServiceMonitor has no
relabeling rules, so Prometheus records PiHole metrics with `instance="<pod-ip>:9666"`.
Follow the pattern from Task 2.8.

**File to modify:**
```
infrastructure/homelab/monitoring/pihole-exporter.yaml
```

Add to the `ServiceMonitor` endpoints entry:
```yaml
relabelings:
  - replacement: pihole
    targetLabel: instance
```

---

## Phase 6 — UniFi Network Metrics

### Multi-Site Deployment Pattern

Phase 6 and 6.5 together form a **per-site UniFi support suite**: one unpoller instance
(time-series metrics), one NetworkOptimizer instance (security audits, RF analysis, speed
tests, threat intelligence), and SIEM logs forwarded to the shared Alloy syslog endpoint.
Each new UniFi site gets its own instances of both; the shared Prometheus/Loki/Grafana stack
collects from all sites.

**Naming convention:** `<component>-<site>` (e.g. `unpoller-homelab`, `unpoller-remote`,
`network-optimizer-homelab`). Use short, stable site names. All per-site secrets, HelmReleases,
and app directories follow this pattern.

**Why one instance per site:**
- Unpoller supports multiple `[[unifi.controller]]` stanzas in a single TOML config, but at
  chart v2.1.x the multi-controller code path has known stability issues (race conditions on
  scrape, stale data after controller reconnect). One HelmRelease per site is safer until
  upstream stabilises.
- NetworkOptimizer is explicitly single-site per deployment (multi-site is upstream issue #204,
  open and not yet in a stable release).
- Per-instance deployment gives independent `instance` labels, independent PodMonitors, and
  independent crash domains — a broken credential for one site doesn't affect the others.

**Remote/cloud API mode:** Unpoller's `remote = true` mode uses a Fabric API key from
`api.ui.com` to auto-discover all consoles. As of chart v2.1.0 this is not yet stable
(rate-limit crashes, nil-pointer panics on NVR/Protect consoles). Use the local
username/password mode for every site.

---

### Task 6.1 — Add unpoller HelmRepository and per-site credentials secrets

Create **one shared read-only local user per site** in each UDM controller UI
(Settings → Admins & Users → Add Admin, select read-only role). Name it something
stable like `monitoring-ro`. Both unpoller and NetworkOptimizer use this same account
— they have identical permission requirements and the same trust level. One account
means one password to rotate per site.

> **Sharing mechanics:** The credentials cannot share a single Kubernetes Secret because
> the two tools consume them differently. Unpoller gets them injected at deploy time via
> `valuesFrom` (the SOPS secret below). NetworkOptimizer gets them entered via its web UI
> on first launch and stored in SQLite — there is no Secret to reference at deploy time
> for the UniFi credentials. "Sharing" just means using the same username/password values
> in both places; do not create a second UniFi account.

**File to create (shared, once):**
```
infrastructure/homelab/monitoring/unpoller-helmrepo.yaml
```

`HelmRepository` in namespace `monitoring`:
- Name: `unpoller`
- URL: `https://unpoller.github.io/helm-chart`
- interval: `24h`

Add to `kustomization.yaml`.

**File to create (one per site):**
```
infrastructure/homelab/monitoring/unpoller-homelab-secret.sops.yaml
infrastructure/homelab/monitoring/unpoller-remote-secret.sops.yaml   # repeat pattern for each site
```

Each secret is a SOPS-encrypted `stringData` field holding the TOML config block
for that site's controller:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: unpoller-homelab-config   # name matches the site; referenced in the HelmRelease valuesFrom
  namespace: monitoring
stringData:
  upConfig: |
    [unifi]
    dynamic = false

    [[unifi.controller]]
    url              = "https://<udm-ip>"   # no :8443 on UDM/UDM-Pro running UniFi OS
    user             = "<read-only-user>"
    pass             = "<password>"
    verify_ssl       = false
    sites            = ["all"]              # auto-discover all sites on this controller
```

> **UDM/UDM-Pro URL note:** UniFi OS devices (UDM, UDM-Pro, UXG) use `https://<ip>`
> without `:8443`. Legacy CloudKey / self-hosted Network Application use `https://<ip>:8443`.
> The unifi library auto-detects the login endpoint from the URL format.

Add both secrets to `kustomization.yaml`.

---

### Task 6.2 — Deploy unpoller (one HelmRelease per site)

**Files to create (one per site):**
```
infrastructure/homelab/monitoring/unpoller-homelab.yaml
infrastructure/homelab/monitoring/unpoller-remote.yaml   # repeat pattern for each site
```

Each is a `HelmRelease` targeting chart `unpoller` from the `unpoller` HelmRepository,
pinned to `~2.x`. The only differences between site files are the `metadata.name`,
the `instance` replacement label, and the `valuesFrom` secret name.

Key Helm values (example for `unpoller-homelab`):

```yaml
podMonitor:
  enabled: true       # chart creates a PodMonitor for scraping (not a ServiceMonitor)
  interval: 30s
  relabelings:
    - replacement: unpoller-homelab   # use the site name so Grafana variables are human-readable
      targetLabel: instance

service:
  enabled: false      # metrics are scraped via pod IP through the PodMonitor

dashboards:
  create: false       # requires Grafana Operator CRDs; use configmap sidecar instead (Task 6.3)

resources:
  requests: { cpu: 50m, memory: 64Mi }
  limits:   { cpu: 100m, memory: 128Mi }
```

Inject the TOML config from the site-specific SOPS secret via `valuesFrom`:

```yaml
valuesFrom:
  - kind: Secret
    name: unpoller-homelab-config   # matches the secret name from Task 6.1
    valuesKey: upConfig
    targetPath: upConfig
```

The chart stores `upConfig` in a Kubernetes `Secret` and mounts it as `/etc/unpoller/up.conf`
inside the container. ARM64 image: `ghcr.io/unpoller/unpoller` ✓.

Add all site HelmReleases to `kustomization.yaml`.

---

### Task 6.3 — UniFi Grafana dashboards

There are **6 official Prometheus dashboards** for unpoller. The original task doc listed
only 4 and had two IDs mislabelled. The complete set:

Download each dashboard JSON from Grafana.com and add to `dashboards/`:

**Files to create:**
```
infrastructure/homelab/monitoring/dashboards/unifi-client-dpi-dashboard.json  # ID 11310 (client DPI)
infrastructure/homelab/monitoring/dashboards/unifi-sites-dashboard.json       # ID 11311 (sites)
infrastructure/homelab/monitoring/dashboards/unifi-usw-dashboard.json         # ID 11312 (switches)
infrastructure/homelab/monitoring/dashboards/unifi-usg-dashboard.json         # ID 11313 (gateway/UDM)
infrastructure/homelab/monitoring/dashboards/unifi-uap-dashboard.json         # ID 11314 (APs)
infrastructure/homelab/monitoring/dashboards/unifi-clients-dashboard.json     # ID 11315 (clients)
```

Add each as a `configMapGenerator` entry in `dashboards/kustomization.yaml` (same
pattern as the existing `node-exporter-full`, `pihole`, and `unbound` entries):
```yaml
- name: dashboard-unifi-client-dpi
  files: [unifi-client-dpi-dashboard.json]
  options:
    disableNameSuffixHash: true
    labels:
      grafana_dashboard: "1"
```
Repeat for each of the six dashboards.

> **Note:** The unpoller Helm chart's built-in dashboard provisioning (`dashboards.create: true`)
> uses Grafana Operator CRs and omits dashboard 11310 (Client DPI). Provisioning via the
> kube-prometheus-stack configmap sidecar gives full control over all 6 dashboards
> and avoids the Grafana Operator dependency.

> **Multi-site in dashboards:** Unpoller already attaches a `site` label to every metric
> (the UniFi site name from the controller). The official dashboards expose a `site` Grafana
> variable; with per-instance deployment you will also have an `instance` variable
> (`unpoller-homelab`, `unpoller-remote`, etc.). No dashboard edits are needed — the existing
> variable filters work across all instances automatically.

---

### Task 6.4 — UniFi SIEM syslog ingestion

The UDM has a built-in SIEM / syslog forwarding option (Settings → System →
Advanced → Remote Logging). This delivers real-time **event logs** (firewall rules
hit, IDS/IPS alerts, client auth, DHCP, VPN) that unpoller does not capture —
unpoller only covers time-series metrics. The two are complementary.

**Approach:** Alloy listens on a UDP syslog port; a MetalLB `LoadBalancer` Service
gives it a stable virtual IP that remains correct regardless of how many nodes are
in the cluster and which node the pod is scheduled on. A DNS A record makes the
target address human-readable and refactorable without reconfiguring the UDM.

**Note:** The Alloy River config for syslog is already implemented in
`alloy.yaml` (syslog listener + `unifi-siem` labels). The remaining work is the
MetalLB Service and DNS record described below.

**File to create:**
```
infrastructure/homelab/monitoring/alloy-syslog-service.yaml
```

A `LoadBalancer` `Service` in namespace `monitoring` that exposes the syslog port
via a dedicated MetalLB IP:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: alloy-syslog
  namespace: monitoring
  annotations:
    metallb.universe.tf/loadBalancerIPs: ${METALLB_SYSLOG_IP}
spec:
  selector:
    app.kubernetes.io/name: alloy
  type: LoadBalancer
  ports:
    - name: syslog-udp
      port: 1514
      targetPort: 1514
      protocol: UDP
```

**Variable to add** to `clusters/homelab/cluster-vars.yaml`:
```yaml
METALLB_SYSLOG_IP: "10.6.1.XX"   # pick a free IP from the MetalLB pool
```

**Remove** the `alloy.extraPorts` `hostPort` block from `alloy.yaml` — it is
superseded by the LoadBalancer Service and is not appropriate for multi-node clusters
(a hostPort binds only on the node where the pod happens to run).

**DNS record (manual step):** Add a static A record in PiHole so the UDM can use
a hostname rather than a raw IP. Edit the PiHole SOPS secret to append a directive
to `FTLCONF_misc_dnsmasq_lines`:
```
address=/syslog.${HOSTNAME}/${METALLB_SYSLOG_IP}
```
This record is explicit and takes precedence over any wildcard in the same dnsmasq
config, so no existing records need to be changed.

**UDM configuration (manual step, one per site):**
In each UDM controller UI: Settings → System → Remote Logging →
set target to `syslog.${HOSTNAME}:1514`, protocol `UDP`, format `syslog`.

The single Alloy syslog endpoint receives from all UDMs. Alloy captures the syslog
`hostname` field as the `host` label, so events from different sites are automatically
distinguishable in Loki without any per-site configuration.

**Grafana:** No dedicated dashboard needed — use the built-in Loki Explore panel.
Filter by site using the `host` label:
- All UniFi SIEM events: `{job="unifi-siem"}`
- Single site: `{job="unifi-siem", host="<udm-hostname>"}`

---

## Phase 6.5 — NetworkOptimizer (per-site)

NetworkOptimizer ([Ozark-Connect/NetworkOptimizer](https://github.com/Ozark-Connect/NetworkOptimizer))
is the second component of the per-site UniFi support suite. It complements unpoller:

| Tool | What it provides |
|------|-----------------|
| unpoller | Time-series metrics (throughput, client counts, signal strength) → Prometheus |
| NetworkOptimizer | Security audits (83+ checks), RF analysis, speed testing, threat intelligence, firewall review → built-in web UI |

NetworkOptimizer is a Blazor Server app backed by SQLite (no external database). It is
single-site per deployment by design (multi-site is upstream issue #204, not yet stable).
Deploy one instance per UniFi site in `apps/homelab/`, following the same
`<component>-<site>` naming convention as unpoller.

**License:** BSL 1.1 — free for personal/non-commercial use on up to 3 sites. Converts to
Apache 2.0 on 2028-01-01.

**No Prometheus metrics endpoint** — the planned InfluxDB integration is not yet implemented.
Alloy collects NetworkOptimizer container logs automatically via the existing Kubernetes pod
log discovery configured in `alloy.yaml` (no per-app Alloy config needed).

**UniFi credentials** are set via the NetworkOptimizer web UI on first launch and stored in
SQLite — they are not injected via environment variables. The only secret to manage is
`APP_PASSWORD` (the initial admin password for the NetworkOptimizer UI itself).

---

### Task 6.5.1 — Deploy NetworkOptimizer (one per site)

**Directory to create (one per site):**
```
apps/homelab/network-optimizer-homelab/
apps/homelab/network-optimizer-remote/   # repeat pattern for each site
```

Each directory contains a `kustomization.yaml` plus the following resources.

**Files per site:**
```
apps/homelab/network-optimizer-homelab/
├── kustomization.yaml
├── deployment.yaml
├── service.yaml
├── pvc.yaml
├── ingressroute.yaml
└── secret.sops.yaml
```

**`pvc.yaml`** — `PersistentVolumeClaim` in namespace `apps`:
```yaml
accessModes: [ReadWriteOnce]
resources:
  requests:
    storage: 10Gi   # SQLite DB + speed test history + JSON logs
```

**`deployment.yaml`** — `Deployment` in namespace `apps`:
- Image: `ozarkconnect/network-optimizer:latest` (ARM64 ✓)
- Ports: 8042 (web UI), 3005 (OpenSpeedTest) — omit 5201 (iperf3) unless needed
- Volume mount: `/data` → the 10Gi PVC
- Liveness/readiness probe: `GET /api/health` port 8042, initialDelaySeconds: 30
- Resources: `requests: {cpu: 100m, memory: 256Mi}`, `limits: {cpu: 500m, memory: 512Mi}`

Key env vars:
```yaml
env:
  - name: BIND_LOCALHOST_ONLY
    value: "false"
  - name: HOST_IP
    value: "${NODE_IP}"           # node IP used for speed test STUN; substitute at reconcile time
  - name: HOST_NAME
    value: "network-optimizer-homelab.${HOSTNAME}"   # canonical FQDN for this instance
  - name: REVERSE_PROXIED_HOST_NAME
    value: "network-optimizer-homelab.${HOSTNAME}"   # same as HOST_NAME when behind Traefik
  - name: TZ
    value: "UTC"
  - name: APP_PASSWORD
    valueFrom:
      secretKeyRef:
        name: network-optimizer-homelab-secret
        key: admin-password
```

**`secret.sops.yaml`** — `Secret` in namespace `apps`, SOPS-encrypted:
```yaml
metadata:
  name: network-optimizer-homelab-secret
stringData:
  admin-password: "<initial-admin-password>"
```

**`service.yaml`** — `ClusterIP` `Service` in namespace `apps`:
- Port 8042 (web UI) — named `http`
- Port 3005 (OpenSpeedTest) — named `speedtest` (optional)

**`ingressroute.yaml`** — Traefik `IngressRoute` routing
`network-optimizer-homelab.${HOSTNAME}` → port 8042.
Follow the pattern in `infrastructure/homelab/dns/pihole-ingressroute.yaml`.

**Wire into Flux:**
Add `- ./network-optimizer-homelab` to `apps/homelab/kustomization.yaml`.

> **First-run:** After the pod starts, log in with `admin` / `<APP_PASSWORD>` and configure
> the UniFi controller URL, username, and password via the UI. These are persisted in
> SQLite (the 10Gi PVC) and survive pod restarts.

> **Remote site access:** NetworkOptimizer needs network reachability to the remote UDM's
> HTTPS management port. If the remote site is not on the same L3 network, a site-to-site
> VPN or UniFi Teleport tunnel is required before this will work.

---

### Task 6.5.2 — Confirm Alloy log collection for NetworkOptimizer

No additional Alloy config is needed. The existing `loki.source.kubernetes "pods"` block
in `alloy.yaml` already discovers and ships all pod logs (including NetworkOptimizer's JSON
logs) to Loki with `namespace`, `pod`, and `container` labels.

To query NetworkOptimizer logs in Loki Explore:
```
{namespace="apps", pod=~"network-optimizer-homelab.*"}
```

NetworkOptimizer writes structured JSON logs — filter by severity, operation, or site using
Loki's `| json` parser and label filters.

---

## Phase 7 — Traefik and Flux CD Metrics

### Task 7.1 — Traefik metrics

**Approach:** Add a dedicated ClusterIP `Service` for Traefik's metrics port so
that a standard `ServiceMonitor` can be used (the conventional pattern). Port 9000
stays absent from the MetalLB LoadBalancer service — it is only reachable
in-cluster via the ClusterIP, which is exactly what Prometheus needs.

**File to modify:**
```
infrastructure/homelab/traefik/helmrelease.yaml
```

Add to Traefik Helm values to enable Prometheus metrics:
```yaml
metrics:
  prometheus:
    entryPoint: traefik    # serves /metrics on :9000
```

**File to create:**
```
infrastructure/homelab/traefik/metrics-service.yaml
```

A ClusterIP `Service` in namespace `traefik` that exposes port 9000 without
touching the MetalLB LoadBalancer service:
```yaml
apiVersion: v1
kind: Service
metadata:
  name: traefik-metrics
  namespace: traefik
spec:
  selector:
    app.kubernetes.io/name: traefik
  ports:
    - name: metrics
      port: 9000
      targetPort: 9000
```

Add `metrics-service.yaml` to `infrastructure/homelab/traefik/kustomization.yaml`.

**File to create:**
```
infrastructure/homelab/monitoring/traefik-servicemonitor.yaml
```

`ServiceMonitor` targeting the `traefik-metrics` ClusterIP service:
```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: traefik
  namespace: monitoring
spec:
  namespaceSelector:
    matchNames: [traefik]
  selector:
    matchLabels:
      app.kubernetes.io/name: traefik-metrics
  endpoints:
    - port: metrics
      path: /metrics
      interval: 30s
      relabelings:
        - sourceLabels: [__meta_kubernetes_pod_node_name]
          targetLabel: instance
```

**Dashboard file to create:**

Download dashboard JSON from Grafana.com ID **17346** and add to `dashboards/`:
```
infrastructure/homelab/monitoring/dashboards/traefik-dashboard.json
```
Add a `configMapGenerator` entry to `dashboards/kustomization.yaml` following the
existing pattern.

---

### Task 7.2 — Flux CD metrics

**File to create:**
```
infrastructure/homelab/monitoring/flux-servicemonitor.yaml
```

`ServiceMonitor` (or multiple) targeting the Flux controller services in
`flux-system` namespace: `source-controller`, `kustomize-controller`,
`helm-controller`, `notification-controller`. Each exposes `:8080/metrics`.
Include a relabeling rule to set `instance` to the controller name
(use `__meta_kubernetes_pod_label_app` or a static `replacement` per endpoint).

**Dashboard file to create:**

Download dashboard JSON from Grafana.com ID **16714** and add to `dashboards/`:
```
infrastructure/homelab/monitoring/dashboards/flux-dashboard.json
```
Add a `configMapGenerator` entry to `dashboards/kustomization.yaml` following the
existing pattern.

---

## Phase 8 — Alert Rules

### Task 8.1 — PrometheusRule for basic alerting

**File to create:**
```
infrastructure/homelab/monitoring/alerting-rules.yaml
```

`PrometheusRule` in namespace `monitoring` with the following alert groups:

**Disk:**
- `NodeFilesystemSpaceLow` — `node_filesystem_avail_bytes / node_filesystem_size_bytes < 0.15`
  for 5 minutes → severity: warning
- `NodeFilesystemSpaceCritical` — `< 0.05` for 5 minutes → severity: critical

**Pods:**
- `KubePodCrashLooping` — rate of `kube_pod_container_status_restarts_total` over 15m > 0
  → severity: warning
- `KubePodNotReady` — `kube_pod_status_ready{condition="false"}` for 10 minutes
  → severity: warning
- `KubeDeploymentReplicasMismatch` — desired != available for 5 minutes
  → severity: warning

**DNS:**
- `PiHoleDown` — `up{job="pihole-exporter"} == 0` for 2 minutes → severity: critical
- `UnboundDown` — `up{job="unbound-exporter"} == 0` for 2 minutes → severity: critical

Alertmanager receiver: leave as the default null receiver (no external
notifications). All alerts are visible in the Grafana Alerting UI and
Alertmanager dashboard.

---

## Phase 9 — Dashboard Kustomization Wiring

### Task 9.1 — Dashboard subdirectory kustomization ✅ (bootstrapped in PR #49)

`dashboards/kustomization.yaml` and `- ./dashboards` in the monitoring
kustomization were created as part of Task 2.5 (Node Exporter dashboard). Add
remaining dashboard files here as they are completed.

All dashboard ConfigMaps must carry:
- `metadata.namespace: monitoring`
- `metadata.labels.grafana_dashboard: "1"`

---

## Phase 10 — OpenTelemetry Collector (custom app support)

This phase adds an **OpenTelemetry Collector** as the universal ingestion point
for hosted applications and custom apps. Apps instrument once with the OTel SDK
and send to a single endpoint; the collector fans out to Prometheus, Loki, and
(in a future Phase 11) Tempo for traces.

```
Custom app / hosted app
  └─ OTel SDK (metrics, logs, traces)
       └─ OTLP → OTel Collector
                    ├─ Prometheus remote_write  (metrics)
                    ├─ Loki push API            (logs)
                    └─ Tempo (future Phase 11)  (traces)
```

Without this layer, every new app would need to independently expose a Prometheus
`/metrics` endpoint and manage its own log shipping.

### Task 10.1 — Add OpenTelemetry Operator HelmRepository

**File to create:**
```
infrastructure/homelab/monitoring/otel-helmrepo.yaml
```

`HelmRepository` for the OpenTelemetry Operator:
- Name: `open-telemetry`
- URL: `https://open-telemetry.github.io/opentelemetry-helm-charts`
- interval: `24h`

---

### Task 10.2 — Deploy OpenTelemetry Operator

**File to create:**
```
infrastructure/homelab/monitoring/otel-operator.yaml
```

`HelmRelease` for chart `opentelemetry-operator`. The operator manages
`OpenTelemetryCollector` custom resources and handles cert rotation.

Key values:
```yaml
manager:
  resources:
    requests: { cpu: 50m, memory: 64Mi }
    limits:   { cpu: 100m, memory: 128Mi }
admissionWebhooks:
  certManager:
    enabled: false     # no cert-manager yet; use operator's self-signed certs
```

OTel Operator images are ARM64-compatible ✓.

---

### Task 10.3 — Deploy OpenTelemetry Collector instance

**File to create:**
```
infrastructure/homelab/monitoring/otel-collector.yaml
```

`OpenTelemetryCollector` CR in namespace `monitoring`, mode `Deployment`.

Receiver / exporter configuration:
```yaml
config:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
        http:
          endpoint: 0.0.0.0:4318
  processors:
    batch: {}
    memory_limiter:
      limit_mib: 128
  exporters:
    prometheusremotewrite:
      endpoint: http://kube-prometheus-stack-prometheus:9090/api/v1/write
    loki:
      endpoint: http://loki:3100/loki/api/v1/push
      default_labels_enabled:
        exporter: false
        job: true
  service:
    pipelines:
      metrics:
        receivers:  [otlp]
        processors: [memory_limiter, batch]
        exporters:  [prometheusremotewrite]
      logs:
        receivers:  [otlp]
        processors: [memory_limiter, batch]
        exporters:  [loki]
```

Resources: `requests: {cpu: 100m, memory: 128Mi}`, `limits: {cpu: 200m, memory: 256Mi}`

---

### Task 10.4 — Expose collector endpoint via Service

The `OpenTelemetryCollector` CR auto-generates a Service, but verify it is
reachable from other namespaces. Apps in any namespace can send to:
- `http://otel-collector.monitoring.svc.cluster.local:4318` (HTTP/OTLP)
- `grpc://otel-collector.monitoring.svc.cluster.local:4317` (gRPC/OTLP)

Document the endpoint addresses in a `ConfigMap` in the `monitoring` namespace
so apps can reference it via environment variable injection rather than
hard-coding the address.

**File to create:**
```
infrastructure/homelab/monitoring/otel-endpoints-configmap.yaml
```

---

### Task 10.5 — Enable Prometheus remote_write receiver

By default, Prometheus does not accept remote_write from external sources.
Add to the kube-prometheus-stack HelmRelease:
```yaml
prometheus:
  prometheusSpec:
    enableRemoteWriteReceiver: true
```

This allows the OTel Collector's `prometheusremotewrite` exporter to push metrics
into the existing Prometheus instance, keeping a single metrics store.

---

### Task 10.6 — (Future — Phase 11) Add Tempo for traces

When distributed tracing is needed:
- Add `grafana/tempo` HelmRelease (single-binary mode, ~4 Gi PVC)
- Add `traces` pipeline to OTel Collector with `otlp` exporter pointing at Tempo
- Add Tempo as a datasource in Grafana
- Add trace-to-log and trace-to-metric correlations in Grafana datasource config

This completes the full LGTM stack.

---

## Verification Checklist

After each phase, run the validation pipeline:
```
./scripts/validate-k3s.sh
```

The pipeline now has 7 steps. Step 7 runs `conftest` against `policy/` and enforces
that every `ServiceMonitor` and `PodMonitor` endpoint has a `targetLabel: instance`
relabeling rule (PR #99). Any new monitor missing this will fail the build.

After full deployment (via Flux reconciliation):

- [ ] `kubectl get pods -n monitoring` — all pods `Running`
- [ ] Grafana accessible at `http://grafana.homelab.home.arpa`
- [ ] Prometheus targets page (`/targets`) — all exporters `UP`
- [ ] Loki datasource in Grafana Explore returns pod logs
- [ ] PiHole dashboard — query counts, blocked %, upstream latency visible
- [ ] Unbound dashboard — query/reply types, cache hit rate visible
- [ ] UniFi dashboard — UDM devices, clients, throughput visible
- [ ] Node exporter dashboard — host CPU, memory, disk, temperature visible
- [ ] K3s dashboard — pod counts, resource usage, apiserver latency visible
- [ ] Traefik dashboard — request rates, error rates visible
- [ ] Flux dashboard — reconciliation status visible
- [ ] Alert rules listed in Alertmanager UI

---

## ARM64 Compatibility Reference

| Component | Image | ARM64 |
|-----------|-------|-------|
| kube-prometheus-stack (all) | prometheus-community | ✓ |
| Loki | grafana/loki | ✓ |
| Alloy | grafana/alloy | ✓ |
| pihole-exporter | amonacoos/pihole6_exporter (bazmonk/pihole6_exporter) | ✓ |
| unbound-exporter | ar51an/unbound-exporter | verify tag |
| unpoller | ghcr.io/unpoller/unpoller | ✓ |
| NetworkOptimizer | ozarkconnect/network-optimizer | ✓ |
| OTel Operator | ghcr.io/open-telemetry/opentelemetry-operator | ✓ |
| OTel Collector | ghcr.io/open-telemetry/opentelemetry-collector-releases/opentelemetry-collector-contrib | ✓ |

---

## Files to Create (complete list)

Files marked `[×N]` are created once per UniFi site. The examples below use `homelab`
and `remote` as site names; substitute the actual site names when implementing.

```
infrastructure/homelab/monitoring/
├── kustomization.yaml                            ✅ DONE
├── namespace.yaml                                ✅ DONE
├── prometheus-helmrepo.yaml                      ✅ DONE
├── grafana-helmrepo.yaml                         ✅ DONE
├── kube-prometheus-stack.yaml                    ✅ DONE (fixups: Tasks 2.7, 2.8)
├── grafana-secret.sops.yaml                      ✅ DONE
├── grafana-ingressroute.yaml                     ✅ DONE
├── loki.yaml                                     ✅ DONE
├── alloy.yaml                                    ✅ DONE (fixup: Task 6.4 hostPort removal)
├── pihole-exporter.yaml                          ✅ DONE (fixup: Task 4.3)
├── pihole-exporter-secret.sops.yaml              ✅ DONE
├── unbound-servicemonitor.yaml                   ✅ DONE (fixup: Task 5.3)
├── unpoller-helmrepo.yaml                        # shared, once
├── unpoller-homelab-secret.sops.yaml             [×N] one per site
├── unpoller-remote-secret.sops.yaml              [×N]
├── unpoller-homelab.yaml                         [×N] one HelmRelease per site
├── unpoller-remote.yaml                          [×N]
├── alloy-syslog-service.yaml
├── traefik-servicemonitor.yaml
├── flux-servicemonitor.yaml
├── alerting-rules.yaml
├── otel-helmrepo.yaml
├── otel-operator.yaml
├── otel-collector.yaml
├── otel-endpoints-configmap.yaml
└── dashboards/
    ├── kustomization.yaml                        ✅ DONE (PR #49)
    ├── node-exporter-full.json                   ✅ DONE (PR #49)
    ├── pihole-dashboard.json                     ✅ DONE
    ├── unbound-dashboard.json                    ✅ DONE
    ├── unifi-client-dpi-dashboard.json           # ID 11310
    ├── unifi-sites-dashboard.json                # ID 11311
    ├── unifi-usw-dashboard.json                  # ID 11312
    ├── unifi-usg-dashboard.json                  # ID 11313
    ├── unifi-uap-dashboard.json                  # ID 11314
    ├── unifi-clients-dashboard.json              # ID 11315
    ├── traefik-dashboard.json                    # ID 17346
    └── flux-dashboard.json                       # ID 16714

apps/homelab/
├── kustomization.yaml                            (add network-optimizer-* entries)
├── network-optimizer-homelab/                    [×N] one directory per site
│   ├── kustomization.yaml
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── pvc.yaml
│   ├── ingressroute.yaml
│   └── secret.sops.yaml
└── network-optimizer-remote/                     [×N]
    └── ...

policy/servicemonitor_instance_label.rego         ✅ DONE (PR #99)
scripts/validate/07-conftest.sh                   ✅ DONE (PR #99)
infrastructure/homelab/kustomization.yaml         ✅ DONE
infrastructure/homelab/traefik/helmrelease.yaml   (add Prometheus metrics config)
infrastructure/homelab/dns/unbound-configmap.yaml ✅ DONE
infrastructure/homelab/dns/unbound-deployment.yaml ✅ DONE
clusters/homelab/cluster-vars.yaml                (add METALLB_SYSLOG_IP)
```
