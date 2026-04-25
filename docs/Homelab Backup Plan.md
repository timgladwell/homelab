# Homelab Backup Plan

## Context

Four stateful workloads hold data that can't be reconstructed from Git alone:
- PiHole config (`dns/pihole-config` PVC, 500Mi)
- Prometheus TSDB (`monitoring/prometheus-db-*` PVC, 14Gi)
- Grafana data (`monitoring/kube-prometheus-stack-grafana` PVC, 2Gi)
- Loki log store (`monitoring/loki` PVC, 8Gi)

The SOPS Age private key is a single point of failure for all secrets. **Requirements**: RPO 15 min (local), 24 h (offsite full rebuild). Daily offsite to Google Drive. Automated. Alert on failure.

---

## Architecture

```
Source data                  Local (15-min, 7-day)         GDrive (daily, long-term)
───────────                  ─────────────────────         ─────────────────────────
PiHole Teleporter API ──────► /backup/repo/pihole  ──┐
Prometheus PVC (hostPath) ──► /backup/repo/prometheus│   daily copy job (03:00)
Grafana PVC (hostPath) ─────► /backup/repo/grafana  ─┤──► gdrive:homelab-backup/repo/
Loki PVC (hostPath) ────────► /backup/repo/loki     ─┘      (daily-7, weekly-4, monthly-3)
                                     │
                              prune nightly (02:00)           Age key + Flux SSH key
                              keep-within 7d                  ──► gdrive:homelab-backup/keys/
```

**Tools**: `restic/restic:0.17.3` + `rclone/rclone:1.68.2` (both ARM64 native). No Helm charts, no MinIO.

---

## Key Technical Decisions

### Data consistency

**PiHole** uses the [Teleporter API](https://ftl.pi-hole.net/api/docs) — PiHole v6 exposes `GET /api/teleporter` which produces a consistent config archive. The CronJob calls this endpoint and pipes the response to `restic backup --stdin`. No PVC mount or hostPath needed; eliminates the read-while-writing concern entirely. If the API requires a session token in the deployed version, fall back to raw PVC (note this in bootstrap steps after testing).

**Grafana** uses SQLite in WAL mode. Hot copies of WAL-mode SQLite are safe: the main database file is always consistent at a read boundary and the WAL is replayed on open. Raw PVC backup is fine.

**Prometheus** TSDB uses a WAL. Prometheus will replay it on startup after any incomplete write. Raw PVC backup is acceptable; strict consistency would require `--web.enable-admin-api` + the snapshot endpoint (future enhancement if needed).

**Loki** chunk-based filesystem store — some tail loss on restoration is acceptable for logs. Raw PVC is fine.

### Local vs. offsite repos — two separate restic repos

The 15-min local snapshots are too granular for offsite (96 snapshots/workload/day). Two separate repos:

| Repo | Location | Cadence | Retention |
|------|----------|---------|-----------|
| Local | `/backup/repo/<workload>/` | Every 15 min | `--keep-within 7d` |
| Offsite | `rclone:gdrive:homelab-backup/repo/<workload>/` | Daily at 03:00 | `--keep-daily 7 --keep-weekly 4 --keep-monthly 3` |

The daily offsite job copies the **latest local snapshot** to the remote repo using `restic copy`, then prunes. This means GDrive stores only one snapshot per day — no incrementals clutter.

### Combined restic+rclone pod (initContainer pattern)

The daily offsite CronJob needs both restic (to run `copy` and `forget`) and rclone (as the backend transport). Both are official images; combine via initContainer copying the rclone binary to a shared emptyDir:

```yaml
initContainers:
  - name: get-rclone
    image: rclone/rclone:1.68.2
    command: [cp, /usr/local/bin/rclone, /shared/rclone]
    volumeMounts:
      - {name: shared-bin, mountPath: /shared}
containers:
  - name: restic
    image: restic/restic:0.17.3
    env:
      - name: PATH
        value: "/shared:/usr/local/bin:/usr/bin:/bin"
    # restic calls rclone as a subprocess for the rclone:// backend
volumes:
  - {name: shared-bin, emptyDir: {}}
```

### Compression

All restic repos initialised with `--compression auto` (zstd, restic 0.14+). Meaningful savings for PiHole config and Grafana SQLite; near-zero benefit for Prometheus TSDB (already compressed) but no harm.

### Cross-namespace PVC access via hostPath

K3s local-path stores PVCs at `/var/lib/rancher/k3s/storage/<pvc-uid>_<namespace>_<pvcname>/`. CronJobs in `backup` namespace can't mount PVCs from `dns` or `monitoring`, so they use hostPath volumes. The four path values go into `cluster-vars.yaml` and are Flux-substituted at reconcile time. PiHole skips this (Teleporter API).

### Static PVs for local restic repos

`/backup/repo/*` directories on the host are exposed as static PV→PVC pairs in the `backup` namespace (`storageClassName: ""`, bound by `claimRef`). Gives capacity tracking and declarative ownership without dynamic provisioning.

---

## Schedules

| CronJob | Schedule | Purpose |
|---------|----------|---------|
| backup-pihole | `0,15,30,45 * * * *` | restic via Teleporter API |
| backup-prometheus | `4,19,34,49 * * * *` | restic of Prometheus PVC |
| backup-grafana | `8,23,38,53 * * * *` | restic of Grafana PVC |
| backup-loki | `12,27,42,57 * * * *` | restic of Loki PVC |
| backup-prune-pihole | `0 2 * * *` | restic forget `--keep-within 7d` |
| backup-prune-prometheus | `10 2 * * *` | same |
| backup-prune-grafana | `20 2 * * *` | same |
| backup-prune-loki | `30 2 * * *` | same |
| backup-offsite | `30 3 * * *` | freshness check + restic copy → GDrive + remote forget |
| backup-key-backup | `0 4 * * *` | Age key + Flux SSH key → GDrive |

### Offsite scheduling rationale

The latest local backup batch starts at 02:57; with `activeDeadlineSeconds: 600` each job finishes by 03:07 at the very latest. Prune jobs finish by 02:40. Scheduling offsite at **03:30** gives a 23-minute buffer and ensures prune has already run (so we're not copying soon-to-be-deleted snapshots).

The offsite job does **not** rely on time alone. It opens by checking that each workload has a snapshot less than 2 hours old before proceeding:

```bash
MAX_AGE_SECONDS=7200   # 2 hours
NOW=$(date +%s)

for WORKLOAD in pihole prometheus grafana loki; do
  SNAP_TIME=$(restic -r /backup/repo/$WORKLOAD snapshots --json --last \
    | grep -o '"time":"[^"]*"' | head -1 | cut -d'"' -f4)
  SNAP_EPOCH=$(date -d "$SNAP_TIME" +%s 2>/dev/null || date -j -f "%Y-%m-%dT%H:%M:%S" "$SNAP_TIME" +%s)
  AGE=$(( NOW - SNAP_EPOCH ))
  if [ "$AGE" -gt "$MAX_AGE_SECONDS" ]; then
    echo "ERROR: $WORKLOAD snapshot is ${AGE}s old (max ${MAX_AGE_SECONDS}s). Aborting offsite backup."
    exit 1
  fi
done
# Proceed with restic copy for all workloads...
```

If any workload is stale, the job exits non-zero. Kubernetes records it as a failed job → `BackupOffsiteStale` or `BackupJobFailed` alert fires. No kubectl or external API calls needed — just restic reading the local repos.

---

## Files to Create

```
infrastructure/homelab/backup/
  kustomization.yaml
  namespace.yaml                  # namespace: backup
  backup-pv.yaml                  # 4 static PVs for local restic repos
  backup-pvc.yaml                 # 4 PVCs bound to above
  rbac.yaml                       # SA + ClusterRole (key-backup reads flux-system secret)
  restic-secret.sops.yaml         # RESTIC_PASSWORD (local repos)
  rclone-secret.sops.yaml         # rclone.conf with GCP service account JSON
  cronjob-pihole.yaml             # Teleporter API → restic --stdin
  cronjob-prometheus.yaml         # hostPath PVC → restic
  cronjob-grafana.yaml            # hostPath PVC → restic
  cronjob-loki.yaml               # hostPath PVC → restic
  cronjob-prune.yaml              # 4 CronJobs (one per repo), staggered from 02:00
  cronjob-offsite.yaml            # restic copy + remote forget (initContainer pattern)
  cronjob-key-backup.yaml         # Age key + Flux SSH key → GDrive
  alerting-rules.yaml             # PrometheusRule
  alertmanager-config.yaml        # AlertmanagerConfig stub (null receiver)
```

## Files to Modify

| File | Change |
|------|--------|
| `infrastructure/homelab/kustomization.yaml` | Add `- ./backup` |
| `clusters/homelab/cluster-vars.yaml` | Add `PROMETHEUS_PVC_PATH`, `GRAFANA_PVC_PATH`, `LOKI_PVC_PATH` (PiHole uses API, no path needed) |

---

## CronJob Spec Patterns

### PVC backup (prometheus/grafana/loki)

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
          containers:
            - name: restic
              image: restic/restic:0.17.3
              args: [backup, /data, --host=homelab, --tag=prometheus]
              env:
                - {name: RESTIC_REPOSITORY, value: /repo}
                - name: RESTIC_PASSWORD
                  valueFrom: {secretKeyRef: {name: restic-credentials, key: RESTIC_PASSWORD}}
              volumeMounts:
                - {name: data, mountPath: /data, readOnly: true}
                - {name: repo, mountPath: /repo}
              resources:
                requests: {cpu: 100m, memory: 64Mi}
                limits:   {cpu: 300m, memory: 150Mi}
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
args: [backup, --stdin, --stdin-filename, pihole-teleporter.tar.gz, --host=homelab, --tag=pihole]
env:
  - {name: RESTIC_REPOSITORY, value: /repo}
  - name: RESTIC_PASSWORD
    valueFrom: {secretKeyRef: {name: restic-credentials, key: RESTIC_PASSWORD}}
  - name: PIHOLE_URL
    value: "http://pihole.dns.svc.cluster.local:8080"
command: [sh, -c, "curl -sf $PIHOLE_URL/api/teleporter | restic backup --stdin --stdin-filename pihole-teleporter.tar.gz"]
# No hostPath volume for source; only the local repo PVC
```

### Daily offsite job (restic copy to GDrive)

```yaml
initContainers:
  - name: get-rclone
    image: rclone/rclone:1.68.2
    command: [cp, /usr/local/bin/rclone, /shared/rclone]
    volumeMounts: [{name: shared-bin, mountPath: /shared}]
containers:
  - name: offsite
    image: restic/restic:0.17.3
    command: [sh, -c]
    args:
      - |
        export PATH="/shared:$PATH"
        for WORKLOAD in pihole prometheus grafana loki; do
          LOCAL_REPO=/repo/$WORKLOAD
          REMOTE_REPO="rclone:gdrive:homelab-backup/repo/$WORKLOAD"
          # Initialise remote repo if first run
          restic -r "$REMOTE_REPO" init --compression auto || true
          # Copy latest local snapshot to remote
          LATEST=$(restic -r "$LOCAL_REPO" snapshots --json --last | \
                   sh -c 'tr -d "\n"' | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
          restic -r "$REMOTE_REPO" copy \
            --from-repo "$LOCAL_REPO" \
            --from-password-env RESTIC_PASSWORD \
            "$LATEST"
          # Prune remote with long-term retention
          restic -r "$REMOTE_REPO" forget --prune \
            --keep-daily 7 --keep-weekly 4 --keep-monthly 3
        done
    env:
      - name: RESTIC_REPOSITORY     # for local repos (not used directly here)
        value: /repo
      - name: RESTIC_PASSWORD
        valueFrom: {secretKeyRef: {name: restic-credentials, key: RESTIC_PASSWORD}}
      - name: RCLONE_CONFIG
        value: /config/rclone/rclone.conf
    volumeMounts:
      - {name: shared-bin, mountPath: /shared}
      - {name: repo, mountPath: /repo, readOnly: true}   # whole /backup/repo tree
      - {name: rclone-config, mountPath: /config/rclone, readOnly: true}
    resources:
      requests: {cpu: 200m, memory: 128Mi}
      limits:   {cpu: 500m, memory: 256Mi}
volumes:
  - {name: shared-bin, emptyDir: {}}
  - name: repo
    hostPath: {path: /backup/repo, type: Directory}
  - name: rclone-config
    secret: {secretName: rclone-credentials}
```

> **Note**: The inline shell to extract the latest snapshot ID is verbose. If `jq` is not available in the restic alpine image, use: `restic -r "$LOCAL_REPO" snapshots --last --json | grep -o '"short_id":"[^"]*"'`. Alternatively, the `restic copy --tag <workload>` approach can be used to copy only the most recent tagged snapshot.

### Alerting rules

kube-state-metrics is already deployed and exposes `kube_cronjob_status_last_successful_time`.

```yaml
# 15-min schedule + 5-min buffer = 20 min staleness threshold
- alert: BackupPiholeStale
  expr: (time() - kube_cronjob_status_last_successful_time{namespace="backup",cronjob="backup-pihole"}) > 1200
  for: 2m
  labels: {severity: warning}

# Repeat pattern for prometheus, grafana, loki

# Daily offsite + 1-hour buffer
- alert: BackupOffsiteStale
  expr: (time() - kube_cronjob_status_last_successful_time{namespace="backup",cronjob="backup-offsite"}) > 90000
  for: 5m
  labels: {severity: critical}

- alert: BackupJobFailed
  expr: kube_job_status_failed{namespace="backup"} > 0
  for: 1m
  labels: {severity: warning}

- alert: BackupRepoDiskSpaceLow
  expr: node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"} < 0.15
  for: 5m
  labels: {severity: warning}
```

---

## One-Time Bootstrap Steps (manual, not GitOps-able)

1. **On the Pi** — create repo directories before PR 1 merges:
   ```bash
   sudo mkdir -p /backup/repo/{pihole,prometheus,grafana,loki}
   sudo chmod 700 /backup/repo
   ```

2. **In GCP** — create service account, grant GDrive folder access, download JSON key.

3. **Lookup PVC host paths** (needed for cluster-vars before PR 2):
   ```bash
   kubectl get pv -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.hostPath.path}{"\n"}{end}'
   ```

4. **After PR 1 merges and Flux reconciles** — init local restic repos with compression:
   ```bash
   PASS=$(kubectl get secret -n backup restic-credentials -o jsonpath='{.data.RESTIC_PASSWORD}' | base64 -d)
   for repo in pihole prometheus grafana loki; do
     restic -r /backup/repo/$repo --password "$PASS" init --compression auto
   done
   ```

5. **Verify PiHole Teleporter API** (unauthenticated access):
   ```bash
   kubectl exec -n backup <any-pod> -- curl -sf http://pihole.dns.svc.cluster.local:8080/api/teleporter -o /tmp/test.tar.gz
   ls -lh /tmp/test.tar.gz
   ```
   If this requires authentication, switch the pihole CronJob to hostPath + raw PVC backup.

---

## PR Sequence

| PR | Contents | Gate |
|----|----------|------|
| 1 — Foundation | namespace, static PVs/PVCs, RBAC, restic-secret, rclone-secret | Manually create dirs, init repos (bootstrap step 4) |
| 2 — Local backups | 4 backup CronJobs + prune CronJobs + cluster-vars PVC paths | Trigger one job manually; verify snapshot with `restic snapshots` |
| 3 — Offsite | offsite CronJob + key-backup CronJob | Trigger manually; verify files in GDrive |
| 4 — Alerting | alerting-rules.yaml + alertmanager-config.yaml | Verify rules in Prometheus UI; intentionally fail a job to confirm alert |
| 5 — Runbooks | `docs/runbooks/`: pvc-restore, full-rebuild, offsite-recovery, age-key-recovery, backup-alert-triage | Review accuracy |

---

## Verification

```bash
# CronJobs all present
kubectl get cronjob -n backup

# Local snapshots accumulating (after PR 2)
restic -r /backup/repo/pihole snapshots --password "$PASS"

# GDrive content (after PR 3)
rclone ls gdrive:homelab-backup/

# Alert rules visible in Prometheus
# → http://prometheus.homelab.home.arpa/rules → look for Backup* group

# Full validation after each PR
./scripts/validate-k3s.sh
```