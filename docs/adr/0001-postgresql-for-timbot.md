# 1. Running PostgreSQL for timbot

- **Status:** Proposed
- **Date:** 2026-09-18
- **Deciders:** Tim Gladwell

## Context

[timbot](https://github.com/timgladwell/timbot) is a Rails monolith holding
small household automations, and the only custom application planned for the
estate. It needs a PostgreSQL database.

timbot's
[ADR 0003](https://github.com/timgladwell/timbot/blob/main/docs/adr/0003-database-as-a-service-from-homelab.md)
makes that database homelab's responsibility in full: the engine version, server
parameters, storage, backups, recovery, alerting and upgrades. timbot consumes a
connection string and nothing else. Its
[ADR 0001](https://github.com/timgladwell/timbot/blob/main/docs/adr/0001-postgresql-as-primary-datastore.md)
chose PostgreSQL as the engine and a single logical database as the shape; this
ADR decides how that database is run here. Much of what follows was first written
in timbot's ADR 0001 and moves here with the responsibility.

Constraints this repo already imposes:

- Akron is a single node (Raspberry Pi 4B, 8GB RAM, 256GB NVMe over USB3) shared
  between production work (DNS) and non-production work (task automation).
  Resource contention is a first-class concern, and DNS wins it.
- All images must be ARM64. All workloads declare requests and limits.
- Everything is reconciled by Flux from this repo; the exceptions are inventoried
  in `docs/host-state.md`.
- Warnings are errors in validation, and secrets are SOPS-encrypted per site.

The requirements timbot places on the database are an online backup process
supporting a daily offsite, point-in-time recovery for the "bad deploy corrupted
data" case, and consistent reliability over throughput. Speed is explicitly not a
factor.

This is the first ADR in this repo. Several existing documents under `docs/`
record decisions in other shapes and are expected to be converted later;
numbering runs from when a decision was written down here, not from when it was
made.

## Decision

### Deployment: the CloudNativePG operator

Postgres runs in K3s via the CloudNativePG operator rather than a hand-rolled
StatefulSet. The operator manages PVCs directly, publishes arm64 operand images,
and — decisively — makes point-in-time recovery a declarative `Cluster` with a
`recoveryTarget`, rather than a hand-typed restore procedure executed under
pressure.

CNPG was accepted into the CNCF at Sandbox level on 2025-01-21, with an
Incubation application submitted at KubeCon NA in November 2025. The Sandbox
label reflects a late donation rather than immaturity: it originated at
EnterpriseDB, lists IBM, Google Cloud and Microsoft Azure among adopters, and
passed 132 million downloads in 2025.

### Where it lives in this repo

Akron-only, and split on CRD ownership the way `traefik`/`traefik-routes` and
`cert-manager`/`cert-manager-config` already are:

| Layer | Holds | Why |
| --- | --- | --- |
| `infrastructure` | The CNPG operator and the Barman Cloud plugin | They install the CRDs, so they cannot ship alongside resources that use them |
| `infrastructure-config` | The `Cluster`, the `ObjectStore`, the application role's Secret | Custom resources. `infrastructure-config` `dependsOn: infrastructure`, so the CRDs exist by the time these apply |

timbot runs only at Akron, so these are site resources under `sites/akron/`
rather than shared components in `base/` — the same placement as Akron's
monitoring storage. If a second site ever needs a database, the operator moves to
`base/` then and not before.

### Topology

One `Cluster`, single instance, dedicated to timbot, with one logical database
and one application role. Storage is a PVC on the NVMe via the K3s local-path
provisioner. The Postgres major version is pinned explicitly, and `shared_buffers`
and related parameters are sized against the Pi's memory budget rather than left
at defaults.

No replication and no HA. A second node does not exist, and a second instance on
this one protects against nothing.

### The interface to timbot: one Secret, in timbot's namespace

The `Cluster` and timbot run in separate namespaces. A Kubernetes Secret is
namespaced and a Pod can only reference Secrets in its own namespace, so the
application Secret CNPG generates is unreachable from timbot by construction.

So this repo owns the credential explicitly:

- The application role's password lives in a SOPS-encrypted file under
  `sites/akron/`, like every other secret here.
- That password is supplied to CNPG for the application role, and written as a
  `DATABASE_URL` into a Secret named `timbot-database` in timbot's namespace.
- timbot's Deployment names that Secret and nothing else. It does not name the
  `Cluster`, the operator, or a CNPG Service.

Letting timbot read CNPG's generated Secret instead — by colocating it in the
database's namespace — would need no machinery at all and was rejected for one
reason: the generated Secret's name identifies a particular `Cluster` object, so
timbot's configuration would name the database instance, and a major-version
cutover would become a change to timbot.

This is a deliberate exception to the usual preference for generated credentials
over hand-managed ones. It is also consistent with *Put only actual secrets in a
Secret* — a password is exactly that.

### Backups and offsite

- Base backups plus continuous WAL archiving to a **new bucket on the existing
  AWS account**, via the CNPG Barman Cloud plugin. Barman Cloud speaks S3
  natively, so no adapter or self-hosted object store is required.
- **S3 Standard storage class.** Standard-IA carries a 30-day minimum storage
  duration and a 128KB minimum billable object size; at two-week retention it
  would cost more, not less. Glacier tiers are 90-day minimums.
- **WAL compression enabled.** WAL segments are a fixed 16MB regardless of
  content, so an idle database still emits padded full-size files. Compression is
  what keeps volume and cost proportional to actual change.
- **`archive_timeout` set to 15 minutes.** This is an RPO dial, not a schedule:
  each tick forces a segment switch and ships a padded 16MB file. 15 minutes caps
  worst-case loss on hardware failure at 15 minutes while cutting segment volume
  roughly two-thirds versus a 5-minute setting, and reduces write wear on the
  USB-attached NVMe.
- **Dedicated IAM user** scoped to the single bucket prefix, no wildcards, so a
  compromised node has a blast radius of one prefix.
- **Bucket versioning on**, so a bug or errant delete cannot irrecoverably erase
  backup history.

Cost at this scale is roughly $0.12–$1.50/month depending on compression
effectiveness. Backblaze B2 is ~70% cheaper per GB but was rejected: the saving
is under a dollar a month and does not justify a second vendor, credential set
and renewal surface.

The bucket and the IAM user are not reconciled by anything in this repo. They are
host state, and belong in `docs/host-state.md` with everything else that fails
silently.

### Retention

- **14 days, configured as the Barman `retentionPolicy`** — not as an S3
  lifecycle rule. Barman tracks which WAL segments the oldest base backup still
  requires. A lifecycle rule deleting by age has no such knowledge and can expire
  required WAL, producing a backup set that looks healthy in the console and
  fails at restore time.
- An S3 lifecycle rule may be set at ~60 days as a runaway-cost backstop only.

Beyond two weeks there is no value in point-in-time granularity. Longer-horizon
GFS retention stays with the restic/borgbackup layer in `docs/backup-plan.md`.

### Resource isolation: DNS wins

Postgres carries explicit memory requests and limits. The DNS workload is given
`requests == limits` (Guaranteed QoS) and a higher `PriorityClass` than the
database and than timbot. Under memory pressure the kubelet then evicts task
automation rather than household DNS.

This protects other workloads *from* the database. It is the same reason Akron
is the only site that stores observability data.

### Alerting

Three alerts, and they are this repo's, not timbot's — they describe the
platform's health, and the first one takes DNS down with it:

- **WAL archive failure.** Postgres will not recycle WAL that has not been
  successfully archived, so a silently failing archive fills the NVMe and takes
  down everything on the node. This is the highest-severity alarm in the stack
  and needs an alert, not a dashboard panel.
- **PVC free space.**
- **Backup age** — last successful base backup older than N hours.

### Major upgrades are a migration, not an upgrade in place

The database is not upgraded under timbot. A new instance is made available and
timbot moves to it, accepting a downtime window measured in minutes:

1. Stand up a test instance on the new major, restored from a backup of the
   current one, and deploy a throwaway timbot against it to confirm
   compatibility.
2. Tear both down.
3. Stand up the production instance on the new major, disconnect clients from the
   old one, take a final backup and restore it.
4. Redeploy timbot against the new connection string — a change to the
   `timbot-database` Secret in this repo, and nothing in timbot.

CNPG expresses steps 1 and 3 declaratively: a `Cluster` with
`bootstrap.initdb.import` runs `pg_dump`/`pg_restore` from the source cluster, so
the migration is a manifest rather than a typed procedure.

Logical replication would cut the window to seconds and is rejected: it requires
`wal_level=logical` permanently, inflating WAL volume on a USB-attached NVMe that
ships every segment offsite; it runs on replication slots, where a stalled
subscriber retains WAL indefinitely and fills the disk — giving the
highest-severity failure mode above a second trigger; and it replicates neither
DDL nor sequence positions, so a cutover that looks clean collides on the first
insert unless every sequence is advanced by hand.

## Consequences

### Positive

- PITR for bad deploys and a bounded-loss offsite for hardware failure come from
  one mechanism and one configuration.
- Restore is a declarative, rehearsable object rather than an undocumented
  runbook.
- The database's manifests are ordinary components here, so they get the full
  fourteen-step validation, SOPS enforcement and dependency coverage. Had they
  lived in timbot and been pulled in at reconcile time, none of that would have
  applied — to the workload where it matters most.
- No additional infrastructure components: no Redis, no self-hosted object store.
  MinIO would have pre-allocated 1Gi per node in single-node topologies (2Gi
  distributed), against a design centre its own guidance puts at 8+ cores and
  128GB RAM. Garage would have been the choice had local S3 been necessary.
- A cutover to a new major, or to a database outside the cluster entirely, is a
  change here and nowhere else.

### Negative / risks

- **USB3-to-NVMe cache-flush integrity.** Some bridge chipsets ignore or
  misreport cache-flush/FUA commands, so Postgres may believe a commit is durable
  while it sits in the enclosure's volatile cache. A power loss then yields a
  corrupt cluster rather than a clean rollback. This is a larger threat to
  reliability than any configuration decision above and should be verified with a
  pull-the-plug test on this specific enclosure.
- **Shared failure domain.** Data and WAL live on the same disk. Anything not yet
  shipped to S3 is lost with the enclosure; `archive_timeout` bounds that window.
- **Archive failure fills the disk and takes DNS with it.** Covered by the first
  alert above, and the reason it is first.
- **Untested backups are not backups.** Restore rehearsal must be periodic and
  deliberate, and the first one happens while the database is still empty.
- **This repo now owns an application's runtime dependency.** A timbot feature
  needing `pg_trgm` or a non-default server parameter is a PR here, and a timbot
  release that assumes it must land afterwards. That lead time is the cost of the
  boundary.
- **One hand-managed password**, referenced twice — by the `Cluster` and by the
  `timbot-database` Secret. If the two drift, timbot fails to authenticate.
- **CNPG's Barman Cloud support has moved from in-core to a plugin**, and older
  "system" images are deprecated. The plugin path is adopted from the start to
  avoid a later migration.

## Alternatives considered

| Option | Rejected because |
| --- | --- |
| Plain StatefulSet with hand-written CronJobs | Fewest components, but hand-rolled WAL archiving and an untested manual restore path is the usual way homelab PITR turns out to be broken |
| The `Cluster` and its configuration held in timbot's repo | Couples the database's configuration to an application's release cycle, and its manifests would bypass every validation step here. Rejected in timbot's ADR 0003 |
| timbot colocated in the database's namespace, reading CNPG's generated Secret | Simpler, but makes timbot's configuration name a particular `Cluster`, so a major-version cutover becomes a timbot change |
| A controller replicating the generated Secret across namespaces | A component to install, run and upgrade, to deliver a value a SOPS file already holds |
| Postgres on the host, outside K3s | Contradicts GitOps as the single source of truth, and puts the database outside validation entirely |
| Local S3 (MinIO / Garage) + rclone to Drive | Unnecessary once an existing AWS account is available; adds RAM, a component, and a shared failure domain |
| Google Drive / iCloud as the backup target | iCloud via rclone needs the real Apple ID password plus 2FA, produces a 30-day trust token requiring interactive reauth, and demands Advanced Data Protection off — a chain that silently dies monthly. Drive lacks object-store semantics and is the wrong shape for a continuous WAL stream |
| Replication or a second instance for HA | There is one node. It protects against nothing |
