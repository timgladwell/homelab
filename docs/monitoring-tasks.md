# Monitoring Stack — Implementation Task List

## Overview

Add metrics collection, log aggregation, and Grafana dashboards to the homelab.
The stack is **Prometheus + Loki + Grafana** (PLG — the practical subset of LGTM for a
single-node cluster; Mimir is skipped in favour of Prometheus, Tempo is out of scope).

All work follows the existing GitOps pattern: Flux CD + Kustomize + HelmReleases,
SOPS-encrypted secrets, subdomain routing via Traefik.

### Target coverage

| Source | Metrics | Logs |
|--------|---------|------|
| K3s system (apiserver, scheduler, controller-manager, etcd) | ✓ | ✓ |
| Homelab host (CPU, memory, disk, network, temperature) | ✓ | ✓ |
| PiHole | ✓ | ✓ |
| Unbound | ✓ | ✓ |
| UniFi network (UDM) | ✓ | — |
| Traefik ingress | ✓ | ✓ |
| Flux CD | ✓ | ✓ |

### Sizing constraints

| Component | PVC | Retention |
|-----------|-----|-----------|
| Prometheus | 14 Gi | 14 days / 12 GB |
| Loki | 8 Gi | 7 days |
| Grafana | 2 Gi | — |
| **Total** | **~24 Gi** | within 20–30 GB budget |

### Key conventions

- Namespace: `monitoring`
- New files land in `infrastructure/homelab/monitoring/`
- Monitoring is wired into the existing `infrastructure` Flux Kustomization
  (same pattern as `dns/` and `traefik/`; no new Flux Kustomization object needed)
- All secrets follow the `*secret.sops.yaml` naming convention and are SOPS-encrypted
- ARM64 compatibility required for all images (Raspberry Pi 4B)

---

## Phase 1 — Foundation

### Task 1.1 — Create monitoring namespace and directory skeleton

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

### Task 1.2 — Add HelmRepositories

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

## Phase 2 — Metrics Stack (Prometheus + Grafana + Alertmanager)

### Task 2.1 — Deploy kube-prometheus-stack

**Files to create:**
```
infrastructure/homelab/monitoring/kube-prometheus-stack.yaml
```

`HelmRelease` targeting chart `kube-prometheus-stack` from the
`prometheus-community` HelmRepository. Pin to the latest `~69.x` release.

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
    retention: 14d
    retentionSize: 12GB
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
      limits:   { cpu: 500m, memory: 1Gi }

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

### Task 2.2 — Grafana admin secret

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

### Task 2.3 — Grafana IngressRoute

**File to create:**
```
infrastructure/homelab/monitoring/grafana-ingressroute.yaml
```

Traefik `IngressRoute` routing `grafana.${DOMAIN}` →
`kube-prometheus-stack-grafana` service on port 80 in namespace `monitoring`.
Follow the pattern in `infrastructure/homelab/dns/pihole-ingressroute.yaml`.

---

### Task 2.4 — K3s control-plane scraping

K3s binds controller-manager, scheduler, and etcd metrics to `127.0.0.1` by
default, making them unreachable from Prometheus. Two options:

**Option A (preferred) — patch K3s config:**
Edit `/etc/rancher/k3s/config.yaml` on the host (outside GitOps) to expose
metrics on all interfaces:
```yaml
kube-controller-manager-arg: "bind-address=0.0.0.0"
kube-scheduler-arg: "bind-address=0.0.0.0"
etcd-arg: "listen-metrics-urls=http://0.0.0.0:2381"
```
Document this as a host-level prerequisite (not managed by Flux).

**Option B — additionalScrapeConfigs:**
Add a `Secret` containing Prometheus `scrape_configs` that target the node IP
directly; reference it in `prometheusSpec.additionalScrapeConfigsSecret`.

**File to create (Option B):**
```
infrastructure/homelab/monitoring/k3s-scrape-configs-secret.sops.yaml
```

Whichever option is chosen, create matching `ServiceMonitor` or scrape-config
entries for `kube-controller-manager`, `kube-scheduler`, and `etcd`.

---

## Phase 3 — Log Aggregation (Loki + Promtail)

### Task 3.1 — Deploy Loki (single-binary)

**File to create:**
```
infrastructure/homelab/monitoring/loki.yaml
```

`HelmRelease` targeting chart `loki` from the `grafana` HelmRepository.
Use `SingleBinary` deployment mode (appropriate for single-node).

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
  limits_config:
    retention_period: 168h    # 7 days
  schema_config:
    configs:
      - from: "2024-01-01"
        store: tsdb
        object_store: filesystem
        schema: v13
        index:
          prefix: loki_index_
          period: 24h
singleBinary:
  replicas: 1
  persistence:
    enabled: true
    size: 8Gi
  resources:
    requests: { cpu: 100m, memory: 128Mi }
    limits:   { cpu: 300m, memory: 512Mi }
```

Loki images are multi-arch (ARM64 ✓).

---

### Task 3.2 — Deploy Promtail

**File to create:**
```
infrastructure/homelab/monitoring/promtail.yaml
```

`HelmRelease` targeting chart `promtail` from the `grafana` HelmRepository.
Runs as a DaemonSet; collects pod logs from `/var/log/pods/` and journal logs.

Key values:
```yaml
config:
  clients:
    - url: http://loki:3100/loki/api/v1/push
  snippets:
    extraScrapeConfigs: |
      - job_name: journal
        journal:
          labels:
            job: systemd-journal
        relabel_configs:
          - source_labels: [__journal__systemd_unit]
            target_label: unit
          - source_labels: [__journal__hostname]
            target_label: host
resources:
  requests: { cpu: 50m, memory: 64Mi }
  limits:   { cpu: 100m, memory: 128Mi }
```

Promtail images are multi-arch (ARM64 ✓).

---

### Task 3.3 — Add Loki datasource to Grafana

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

## Phase 4 — PiHole Metrics

### Task 4.1 — Deploy pihole-exporter

**File to create:**
```
infrastructure/homelab/monitoring/pihole-exporter.yaml
```

Contains three resources in namespace `monitoring`:

1. `Deployment` — image `ekofr/pihole-exporter:latest` (ARM64 ✓)
   - Env: `PIHOLE_HOSTNAME=pihole.dns.svc.cluster.local`, `PIHOLE_PORT=8080`,
     `INTERVAL=30s`
   - Resources: `requests: {cpu: 20m, memory: 32Mi}`, `limits: {cpu: 50m, memory: 64Mi}`

2. `Service` — port 9617

3. `ServiceMonitor` — targets the Service above, scrape interval 30s,
   namespace `monitoring`

---

### Task 4.2 — PiHole Grafana dashboard

**File to create:**
```
infrastructure/homelab/monitoring/dashboards/pihole-dashboard.yaml
```

`ConfigMap` in namespace `monitoring` with label `grafana_dashboard: "1"`.
Fetch dashboard JSON from Grafana.com ID **10176**
(PiHole Exporter dashboard for Prometheus).

---

## Phase 5 — Unbound Metrics

### Task 5.1 — Enable Unbound statistics and add exporter sidecar

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

### Task 5.2 — Unbound Grafana dashboard

**File to create:**
```
infrastructure/homelab/monitoring/dashboards/unbound-dashboard.yaml
```

`ConfigMap` with label `grafana_dashboard: "1"`.
Grafana.com dashboard ID **11705**.

---

## Phase 6 — UniFi Network Metrics

### Task 6.1 — UniFi credentials secret

Create a **read-only local user** in the UDM controller UI (Settings →
Admins & Users → Add Admin, select read-only role).

**File to create:**
```
infrastructure/homelab/monitoring/unpoller-secret.sops.yaml
```

`Secret` in namespace `monitoring`, SOPS-encrypted fields:
- `UP_UNIFI_DEFAULT_URL` — `https://<udm-ip>`
- `UP_UNIFI_DEFAULT_USER`
- `UP_UNIFI_DEFAULT_PASS`

---

### Task 6.2 — Deploy unpoller

**File to create:**
```
infrastructure/homelab/monitoring/unpoller.yaml
```

Contains three resources in namespace `monitoring`:

1. `Deployment` — image `ghcr.io/unpoller/unpoller:latest` (ARM64 ✓)
   - Env from `unpoller-secret`: `UP_UNIFI_DEFAULT_URL`, `UP_UNIFI_DEFAULT_USER`,
     `UP_UNIFI_DEFAULT_PASS`
   - Additional env (plain): `UP_UNIFI_DEFAULT_VERIFY_SSL=false`,
     `UP_INFLUXDB_DISABLE=true`, `UP_PROMETHEUS_NAMESPACE=unpoller`
   - Resources: `requests: {cpu: 50m, memory: 64Mi}`, `limits: {cpu: 100m, memory: 128Mi}`

2. `Service` — port 9130

3. `ServiceMonitor` — targets the Service, scrape interval 30s

---

### Task 6.3 — UniFi Grafana dashboards

**Files to create:**
```
infrastructure/homelab/monitoring/dashboards/unifi-uap-dashboard.yaml    # ID 11314 (APs)
infrastructure/homelab/monitoring/dashboards/unifi-usw-dashboard.yaml    # ID 11315 (switches)
infrastructure/homelab/monitoring/dashboards/unifi-usg-dashboard.yaml    # ID 11313 (gateway/UDM)
infrastructure/homelab/monitoring/dashboards/unifi-clients-dashboard.yaml # ID 11310 (clients)
```

Each is a `ConfigMap` with label `grafana_dashboard: "1"` in namespace `monitoring`.

---

## Phase 7 — Traefik and Flux CD Metrics

### Task 7.1 — Traefik metrics

**File to modify:**
```
infrastructure/homelab/traefik/helmrelease.yaml
```

Add to Traefik Helm values:
```yaml
metrics:
  prometheus:
    entryPoint: traefik    # expose on the internal :9000 entrypoint
```

**File to create:**
```
infrastructure/homelab/monitoring/traefik-servicemonitor.yaml
```

`ServiceMonitor` targeting the `traefik` service in namespace `traefik` on port
`traefik` (9000). Set `namespaceSelector` to match the `traefik` namespace.

**Dashboard file to create:**
```
infrastructure/homelab/monitoring/dashboards/traefik-dashboard.yaml
```

Grafana.com dashboard ID **17346**.

---

### Task 7.2 — Flux CD metrics

**File to create:**
```
infrastructure/homelab/monitoring/flux-servicemonitor.yaml
```

`ServiceMonitor` (or multiple) targeting the Flux controller services in
`flux-system` namespace: `source-controller`, `kustomize-controller`,
`helm-controller`, `notification-controller`. Each exposes `:8080/metrics`.

**Dashboard file to create:**
```
infrastructure/homelab/monitoring/dashboards/flux-dashboard.yaml
```

Grafana.com dashboard ID **16714**.

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

### Task 9.1 — Dashboard subdirectory kustomization

**File to create:**
```
infrastructure/homelab/monitoring/dashboards/kustomization.yaml
```

Lists all dashboard ConfigMap files as resources.

**File to modify:**
```
infrastructure/homelab/monitoring/kustomization.yaml
```

Add `- ./dashboards` to resources.

All dashboard ConfigMaps must carry:
- `metadata.namespace: monitoring`
- `metadata.labels.grafana_dashboard: "1"`

---

## Verification Checklist

After each phase, run the validation pipeline:
```
./scripts/validate-k3s.sh
```

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
| Promtail | grafana/promtail | ✓ |
| pihole-exporter | ekofr/pihole-exporter | ✓ |
| unbound-exporter | ar51an/unbound-exporter | verify tag |
| unpoller | ghcr.io/unpoller/unpoller | ✓ |

---

## Files to Create (complete list)

```
infrastructure/homelab/monitoring/
├── kustomization.yaml
├── namespace.yaml
├── prometheus-helmrepo.yaml
├── grafana-helmrepo.yaml
├── kube-prometheus-stack.yaml
├── grafana-secret.sops.yaml
├── grafana-ingressroute.yaml
├── k3s-scrape-configs-secret.sops.yaml   (if Option B chosen for Task 2.4)
├── loki.yaml
├── promtail.yaml
├── pihole-exporter.yaml
├── unbound-servicemonitor.yaml
├── unpoller-secret.sops.yaml
├── unpoller.yaml
├── traefik-servicemonitor.yaml
├── flux-servicemonitor.yaml
├── alerting-rules.yaml
└── dashboards/
    ├── kustomization.yaml
    ├── pihole-dashboard.yaml
    ├── unbound-dashboard.yaml
    ├── unifi-uap-dashboard.yaml
    ├── unifi-usw-dashboard.yaml
    ├── unifi-usg-dashboard.yaml
    ├── unifi-clients-dashboard.yaml
    ├── traefik-dashboard.yaml
    └── flux-dashboard.yaml

infrastructure/homelab/kustomization.yaml   (add - ./monitoring)
infrastructure/homelab/traefik/helmrelease.yaml   (add Prometheus metrics config)
infrastructure/homelab/dns/unbound-configmap.yaml (add stats + remote-control)
infrastructure/homelab/dns/unbound-deployment.yaml (add exporter sidecar)
```
