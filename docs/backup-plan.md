# Backup Plan

## Context

Four stateful workloads hold data that cannot be reconstructed from Git alone:

| Workload | PVC | Size |
|----------|-----|------|
| PiHole | `dns/pihole-config` | 500Mi |
| Prometheus | `monitoring/prometheus-db-*` | 14Gi |
| Grafana | `monitoring/kube-prometheus-stack-grafana` | 2Gi |
| Loki | `monitoring/loki` | 8Gi |

The SOPS Age private key is a single point of failure — losing it makes every encrypted secret permanently unreadable.

**Requirements**: RPO 15 min (local PVC corruption), 24 h (full cluster rebuild). Daily offsite to Google Drive. Fully automated. Alert on any failure.

---

## Architecture

```
Source data                  Local (15-min, 7-day)         GDrive (daily, long-term)
───────────                  ─────────────────────         ─────────────────────────
PiHole Teleporter API ──────► /backup/repo/pihole  ──┐
Prometheus PVC (hostPath) ──► /backup/repo/prometheus│   offsite job (03:30 daily)
Grafana PVC (hostPath) ─────► /backup/repo/grafana  ─┤──► gdrive:homelab-backup/repo/
Loki PVC (hostPath) ────────► /backup/repo/loki     ─┘      (daily-7, weekly-4, monthly-3)
                                       │
                               prune 02:00–02:30             Age key + Flux SSH key
                               --keep-within 7d              ──► gdrive:homelab-backup/keys/
```

**Tools**: `restic/restic:0.17.3` + `rclone/rclone:1.68.2` (both ARM64 native). No Helm charts, no MinIO.

---

## Key Design Decisions

### Data consistency per workload

| Workload | Approach | Rationale |
|----------|----------|-----------|
| PiHole | `GET /api/teleporter` piped to `restic --stdin` | PiHole v6 native backup produces a consistent archive; no PVC mount needed; no read-while-writing risk. Fall back to raw PVC if endpoint requires auth. |
| Prometheus | Raw PVC via hostPath | TSDB WAL replay handles incomplete writes on startup. Admin API snapshot is a future enhancement if strict consistency is needed. |
| Grafana | Raw PVC via hostPath | SQLite WAL mode makes hot copies safe — main DB file is always consistent at a read boundary. |
| Loki | Raw PVC via hostPath | Chunk-based store; some tail loss acceptable for logs. |

### Two separate restic repos (local + offsite)

96 snapshots/workload/day is too much for GDrive. Two repos with different retention:

| Repo | Location | Cadence | Retention |
|------|----------|---------|-----------|
| Local | `/backup/repo/<workload>/` | Every 15 min | `--keep-within 7d` |
| Offsite | `rclone:gdrive:homelab-backup/repo/<workload>/` | Daily copy of latest | `--keep-daily 7 --keep-weekly 4 --keep-monthly 3` |

The daily offsite job uses `restic copy` to transfer only the latest local snapshot to a separate GDrive-backed restic repo, then prunes with the long-term policy. GDrive receives one snapshot per workload per day.

### Offsite freshness gate

The offsite CronJob does not rely on timing alone. Each backup CronJob touches a sentinel file on success (`/repo/.backup-ok-YYYYMMDD`). The offsite job reads these before copying — if any workload is missing a sentinel from today, it exits non-zero and the `BackupJobFailed` alert fires. No kubectl needed; restic reads the local repos directly.

### Combined restic+rclone pod (initContainer pattern)

The offsite CronJob needs both tools. An initContainer copies the rclone binary from `rclone/rclone:1.68.2` into a shared emptyDir, then the restic container extends its `PATH` to include it. restic calls rclone as a subprocess for `rclone://` backends.

### Cross-namespace PVC access via hostPath

K3s local-path PVCs live at `/var/lib/rancher/k3s/storage/<pvc-uid>_<namespace>_<pvcname>/`. CronJobs in the `backup` namespace cannot mount PVCs from other namespaces, so they use `hostPath` volumes. The three paths (prometheus, grafana, loki) are stored in `cluster-vars.yaml` and substituted by Flux at reconcile time. PiHole uses the Teleporter API so needs no hostPath.

Paths must be looked up after the PVCs are provisioned:
```bash
kubectl get pv -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.hostPath.path}{"\n"}{end}'
```

### Static PVs for local restic repos

The `/backup/repo/*` host directories are represented as static PV→PVC pairs (`storageClassName: ""`, bound by `claimRef`) in the `backup` namespace. This gives Kubernetes capacity tracking and clean declarative ownership without dynamic provisioning.

### Compression

All repos initialised with `--compression auto` (zstd, restic 0.14+). Benefits PiHole config and Grafana SQLite; negligible effect on already-compressed Prometheus TSDB.

---

## Schedules

| CronJob | Schedule | Purpose |
|---------|----------|---------|
| `backup-pihole` | `0,15,30,45 * * * *` | restic via Teleporter API → local repo |
| `backup-prometheus` | `4,19,34,49 * * * *` | restic of Prometheus PVC → local repo |
| `backup-grafana` | `8,23,38,53 * * * *` | restic of Grafana PVC → local repo |
| `backup-loki` | `12,27,42,57 * * * *` | restic of Loki PVC → local repo |
| `backup-prune-pihole` | `0 2 * * *` | `restic forget --keep-within 7d` |
| `backup-prune-prometheus` | `10 2 * * *` | same |
| `backup-prune-grafana` | `20 2 * * *` | same |
| `backup-prune-loki` | `30 2 * * *` | same |
| `backup-offsite` | `30 3 * * *` | freshness check + `restic copy` → GDrive + remote forget |
| `backup-key-backup` | `0 4 * * *` | Age key + Flux SSH key → GDrive |

**Scheduling rationale**: Last backup batch starts at 02:57; with `activeDeadlineSeconds: 600` it finishes by 03:07 at the latest. Prune finishes by 02:40. Offsite at 03:30 gives a 23-minute buffer and always runs after prune (so it never copies snapshots about to be deleted). All CronJobs use `concurrencyPolicy: Forbid` and `backoffLimit: 0`.

---

## Files to Create

```
infrastructure/homelab/backup/
  kustomization.yaml
  namespace.yaml                  # namespace: backup
  backup-pv.yaml                  # 4 static PVs (pihole/prometheus/grafana/loki repos)
  backup-pvc.yaml                 # 4 PVCs bound to above PVs
  rbac.yaml                       # ServiceAccount + Role/RoleBinding for key-backup
  restic-secret.sops.yaml         # RESTIC_PASSWORD
  rclone-secret.sops.yaml         # rclone.conf + service-account.json
  cronjob-pihole.yaml             # Teleporter API → restic --stdin
  cronjob-prometheus.yaml         # hostPath PVC → restic
  cronjob-grafana.yaml            # hostPath PVC → restic
  cronjob-loki.yaml               # hostPath PVC → restic
  cronjob-prune.yaml              # 4 CronJobs staggered from 02:00
  cronjob-offsite.yaml            # freshness check + restic copy + remote forget
  cronjob-key-backup.yaml         # Age key + Flux SSH key → GDrive via rclone
  alerting-rules.yaml             # PrometheusRule (namespace: monitoring)
  alertmanager-config.yaml        # AlertmanagerConfig stub (namespace: monitoring)

docs/runbooks/
  pvc-restore.md                  # Restore a single PVC from local backup
  full-rebuild.md                 # Full cluster rebuild from scratch
  offsite-recovery.md             # Recover local repo from GDrive
  age-key-recovery.md             # Age key loss scenarios
  backup-alert-triage.md          # Alert triage and common failure modes
```

### Files to modify

| File | Change |
|------|--------|
| `infrastructure/homelab/kustomization.yaml` | Add `- ./backup` |
| `clusters/homelab/cluster-vars.yaml` | Add `PROMETHEUS_PVC_PATH`, `GRAFANA_PVC_PATH`, `LOKI_PVC_PATH` |

---

## CronJob Spec Patterns

### PVC backup (prometheus / grafana / loki)

```yaml
spec:
  schedule: "4,19,34,49 * * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      backoffLimit: 0
      activeDeadlineSeconds: 600
      template:
        spec:
          restartPolicy: Never
          serviceAccountName: backup
          containers:
            - name: restic
              image: restic/restic:0.17.3
              command: [sh, -c]
              args:
                - |
                  set -e
                  restic backup /data --host=homelab --tag=prometheus
                  touch /repo/.backup-ok-$(date +%Y%m%d)
              env:
                - {name: RESTIC_REPOSITORY, value: /repo}
                - name: RESTIC_PASSWORD
                  valueFrom: {secretKeyRef: {name: restic-credentials, key: RESTIC_PASSWORD}}
              volumeMounts:
                - {name: data, mountPath: /data, readOnly: true}
                - {name: repo, mountPath: /repo}
              resources:
                requests: {cpu: 200m, memory: 128Mi}
                limits:   {cpu: 500m, memory: 300Mi}
          volumes:
            - name: data
              hostPath:
                path: "${PROMETHEUS_PVC_PATH}"
                type: Directory
            - name: repo
              persistentVolumeClaim:
                claimName: backup-repo-prometheus
```

### PiHole backup (Teleporter API)

```yaml
command: [sh, -c]
args:
  - |
    set -e
    TMP=$(mktemp)
    trap "rm -f $TMP" EXIT
    wget -q -O "$TMP" http://pihole.dns.svc.cluster.local:8080/api/teleporter
    restic backup --stdin --stdin-filename pihole-teleporter.tar.gz \
      --host=homelab --tag=pihole < "$TMP"
    touch /repo/.backup-ok-$(date +%Y%m%d)
# No hostPath for source data — only the local repo PVC is mounted
```

### Daily offsite job

```yaml
initContainers:
  - name: get-rclone
    image: rclone/rclone:1.68.2
    command: [cp, /usr/local/bin/rclone, /shared/rclone]
    volumeMounts: [{name: shared-bin, mountPath: /shared}]
containers:
  - name: restic
    image: restic/restic:0.17.3
    command: [sh, -c]
    args:
      - |
        set -e
        export PATH="/shared:$PATH"

        # Freshness gate
        TODAY=$(date +%Y%m%d)
        for WORKLOAD in pihole prometheus grafana loki; do
          if [ ! -f "/repo/${WORKLOAD}/.backup-ok-${TODAY}" ]; then
            echo "ERROR: No successful backup for ${WORKLOAD} today. Aborting."
            exit 1
          fi
        done

        # Copy latest snapshot to GDrive and prune remote
        for WORKLOAD in pihole prometheus grafana loki; do
          LOCAL="/repo/${WORKLOAD}"
          REMOTE="rclone:gdrive:homelab-backup/repo/${WORKLOAD}"
          restic -r "$REMOTE" init --compression auto 2>/dev/null || true
          LATEST=$(restic -r "$LOCAL" snapshots --json --last \
            | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
          restic -r "$REMOTE" copy --from-repo "$LOCAL" \
            --from-password-env RESTIC_PASSWORD "$LATEST"
          restic -r "$REMOTE" forget --prune \
            --keep-daily 7 --keep-weekly 4 --keep-monthly 3
        done
    env:
      - {name: RESTIC_PASSWORD, valueFrom: {secretKeyRef: {name: restic-credentials, key: RESTIC_PASSWORD}}}
      - {name: RCLONE_CONFIG, value: /config/rclone/rclone.conf}
volumes:
  - {name: shared-bin, emptyDir: {}}
  - name: repo
    hostPath: {path: /backup/repo, type: Directory}
  - name: rclone-config
    secret: {secretName: rclone-credentials}
```

---

## Alerting Rules

kube-state-metrics (already deployed via kube-prometheus-stack) exposes `kube_cronjob_status_last_successful_time`.

| Alert | Expression | Threshold | Severity |
|-------|-----------|-----------|---------|
| `BackupPiholeStale` | `time() - last_success > 1200` | 20 min (15-min schedule + 5-min buffer) | warning |
| `BackupPrometheusStale` | same pattern | same | warning |
| `BackupGrafanaStale` | same pattern | same | warning |
| `BackupLokiStale` | same pattern | same | warning |
| `BackupOffsiteStale` | `time() - last_success > 90000` | 25 h | critical |
| `BackupJobFailed` | `kube_job_status_failed{namespace="backup"} > 0` | any failure | warning |
| `BackupRepoDiskSpaceLow` | root fs < 15% free | 5 min | warning |

`AlertmanagerConfig` routes `Backup.*` alerts to a null receiver stub — fill in with Slack/email later.

---

## One-Time Bootstrap Steps (manual, not GitOps-able)

**Before PR 1 merges** — on the Pi:
```bash
sudo mkdir -p /backup/repo/{pihole,prometheus,grafana,loki}
sudo chmod 700 /backup/repo
```

**In GCP**: Create a service account, grant it Editor access to a specific GDrive folder, download the JSON key.

**Before PR 2** — look up actual PVC host paths to fill into `cluster-vars.yaml`:
```bash
kubectl get pv -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.hostPath.path}{"\n"}{end}'
```

**After PR 1 merges and Flux reconciles** — initialise each restic repo:
```bash
PASS=$(kubectl get secret -n backup restic-credentials \
  -o jsonpath='{.data.RESTIC_PASSWORD}' | base64 -d)
for repo in pihole prometheus grafana loki; do
  restic -r /backup/repo/$repo --password "$PASS" init --compression auto
done
```

**Verify PiHole Teleporter API** — confirm unauthenticated access works:
```bash
curl -sf http://pihole.dns.svc.cluster.local:8080/api/teleporter -o /tmp/test.tar.gz \
  && ls -lh /tmp/test.tar.gz
```
If auth is required, switch `cronjob-pihole.yaml` to raw PVC + hostPath.

---

## PR Sequence

| PR | Contents | Gate before merging |
|----|----------|-------------------|
| **1 — Foundation** | namespace, static PVs/PVCs, RBAC, secret stubs | Create `/backup/repo/*` dirs on Pi; fill in and encrypt secrets; init restic repos |
| **2 — Local backups** | 4 backup CronJobs + 4 prune CronJobs + `cluster-vars` PVC paths | Trigger one job manually; verify with `restic snapshots` |
| **3 — Offsite** | offsite CronJob + key-backup CronJob | Trigger manually; verify files appear in GDrive |
| **4 — Alerting** | `alerting-rules.yaml` + `alertmanager-config.yaml` | Verify rules in Prometheus UI; intentionally fail a job to confirm alert fires |
| **5 — Runbooks** | `docs/runbooks/` (5 files) | Review for accuracy against deployed paths |

---

## Verification

```bash
# After PR 2
kubectl get cronjob -n backup
restic -r /backup/repo/pihole snapshots --password "$PASS"

# After PR 3
rclone ls gdrive:homelab-backup/

# After PR 4: Prometheus UI → /rules → Backup* group present and not firing

# After each PR
./scripts/validate-k3s.sh
```
