# 1. PostgreSQL as a platform service

- **Status:** Accepted
- **Date:** 2026-09-18
- **Deciders:** Tim Gladwell

## Context

Applications in the estate get PostgreSQL the way they would from a cloud
provider: as a service. The platform owns the engine version, parameters,
storage, backups, recovery, alerting and upgrades. The application owns a
compatible client and receives a connection string. Nothing else crosses the
boundary.

The first and only tenant is [timbot](https://github.com/timgladwell/timbot), a
Rails monolith of household automations. Its
[ADR 0001](https://github.com/timgladwell/timbot/blob/main/docs/adr/0001-postgresql-as-primary-datastore.md)
chose PostgreSQL and a single logical database;
[ADR 0003](https://github.com/timgladwell/timbot/blob/main/docs/adr/0003-database-as-a-service-from-homelab.md)
made the database this repo's responsibility in full. This ADR decides how the
service is run, and what a tenant can rely on. Much of it was first written in
timbot's ADR 0001 and moved here with the responsibility.

Constraints this repo already imposes:

- Akron is a single node (Raspberry Pi 4B, 8GB RAM, 256GB NVMe over USB3) shared
  between production work (DNS) and non-production work (task automation).
  Resource contention is a first-class concern, and DNS wins it.
- All images must be ARM64. All workloads declare requests and limits.
- Everything is reconciled by Flux from this repo, except configuration that
  cannot be: settings on a node, and accounts with outside services (UniFi,
  Cloudflare, and now AWS). Those are inventoried in `docs/host-state.md`.
- Warnings are errors in validation, and secrets are SOPS-encrypted per site.

The data is not valuable enough to justify real cost — that is why it runs on a
single-node cluster at home — and that bounds every decision below: best
practice in shape, at the smallest scale that keeps the shape honest.

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

### Topology: one shared instance, a database and two roles per tenant

One `Cluster`, single instance, at Akron. Each tenant gets its own database and
two roles: a read-write role that owns the database, and a read-only role. The
read-only role exists so tenants are built in the shape that scales reads out to
replicas, even though this instance has none. Tenants are isolated by Postgres's
database/role boundary, not by instance: a second instance would cost a second
set of memory, backups and alerts for isolation this data does not need.

Storage is a PVC on the NVMe via the K3s local-path provisioner. The Postgres
major version is pinned explicitly.

No replication and no HA. A second node does not exist, and a second instance on
this one protects against nothing.

Onboarding a second tenant is a database, two roles and a Secret — see the contract
below. Nothing is built for it until it exists.

### Where it lives in this repo

Akron-only, under `sites/akron/` rather than `base/` — the same placement as
Akron's monitoring storage. If a second site ever needs a database, the operator
moves to `base/` then and not before.

| Flux layer | Holds | Why |
| --- | --- | --- |
| `infrastructure` | The CNPG operator and the Barman Cloud plugin | They install the CRDs, so they cannot ship alongside resources that use them |
| `databases` (new) | The `Cluster`, the `ObjectStore`, each tenant's role passwords and connection Secret | Custom resources, and PVC-backed state |

`databases` `dependsOn` both `infrastructure` and `infrastructure-config`: the
database starts only once the cluster's infrastructure is both installed and
configured. The plugin's mTLS certificates are expected to come from
cert-manager, whose issuers live in `infrastructure-config`.

It is a layer of its own, rather than part of `infrastructure-config` where the
CRD split alone would put it, because of what pruning does to state. A Flux
`Kustomization` that stops listing a resource deletes it, and moving resources
between `Kustomization`s has already cost this repo its Prometheus history.
Keeping the database in a `Kustomization` that changes only when the database
does keeps unrelated reorganisation away from it. The cost is that a stuck
`infrastructure-config` reconcile blocks changes to the database — not the
running database itself.

### The tenant contract

What every tenant gets, and the whole of what it may depend on:

| | |
| --- | --- |
| **Connection** | A Secret named `<tenant>-database` in the tenant's namespace — the Kubernetes namespace at Akron where the tenant's own workloads run, with two keys: `DATABASE_URL` for the read-write role and `DATABASE_READONLY_URL` for the read-only one. TLS is on (`sslmode=require`). The tenant names that Secret and nothing else — not the `Cluster`, the operator, or a CNPG Service |
| **Version** | PostgreSQL **18**. A tenant pins its development and CI to the major published here, and follows when it changes. A new major is adopted after it is GA *and* every tenant's framework supports it — for Rails, that trails GA |
| **Point-in-time recovery** | Continuous WAL archiving, so any moment in the last 14 days can be restored. This mechanism is the commitment |
| **Recovery point** | A target, not a commitment: currently at most 15 minutes of committed data lost on total loss of the node. It is tuned against AWS cost and the Pi's resources, and may be lengthened (see *Cost control*) |
| **Recovery time** | The *capability* to restore within hours, given working hardware, and that is what drills prove. On hardware loss, realistic recovery is days: replacement hardware has to be bought and shipped, or tenants move to a cloud database instead. Nobody is on call |
| **Maintenance** | Minor updates restart the instance; tenants must reconnect, which Rails does. A major upgrade is a migration with a downtime window, and new URLs in the same Secret |
| **Requests** | Extensions, non-default parameters and restores are requested with an issue in this repo. A tenant release that depends on one lands after it |

This is the shape managed platforms use: the platform injects connection details
into the application's environment, and the application reads one variable.
Heroku's Postgres add-on attaching `DATABASE_URL` to an app is the closest
analogue.

The Secret has to be written explicitly because the `Cluster` and the tenant
live in separate namespaces. A Pod can only reference Secrets in its own
namespace, so the application Secret CNPG generates is unreachable from the
tenant by construction. Instead:

- The tenant roles' passwords live in a SOPS-encrypted file under
  `sites/akron/`, like every other secret here.
- They are supplied to CNPG for the roles, and written as `DATABASE_URL` and
  `DATABASE_READONLY_URL` into the `<tenant>-database` Secret in the tenant's
  namespace.
- The read-only URL does not use CNPG's `-ro` Service, which targets replicas
  only and has no endpoints on a single-instance `Cluster`.

Letting a tenant read CNPG's generated Secret instead — by colocating it in the
database's namespace — would need no machinery and was rejected for one reason:
the generated Secret's name identifies a particular `Cluster` object, so the
tenant's configuration would name the database instance, and a major-version
cutover would become a change to the tenant.

Within this repo it is an exception to the usual preference for generated
credentials over hand-managed ones, made deliberately. It is also consistent with *Put only actual secrets in a
Secret* — a password is exactly that.

### Backups and offsite

Continuous WAL archiving straight to S3 is the only backup tier. Every other
shape — a local repository with a periodic offsite copy, a self-hosted object
store — means more software running on the Pi, and a local copy on the only disk
shares its failure domain anyway.

- Base backups plus continuous WAL archiving to a **new bucket on the existing
  AWS account**, via the CNPG Barman Cloud plugin. Barman Cloud speaks S3
  natively, so no adapter or self-hosted object store is required.
- **The plugin, not the in-core path.** CNPG's Barman Cloud support has moved
  from in-core to a plugin, and older "system" images are deprecated. Adopting
  the plugin from the start avoids a later migration.
- **S3 Standard storage class.** Standard-IA carries a 30-day minimum storage
  duration and a 128KB minimum billable object size; at two-week retention it
  would cost more, not less. Glacier tiers are 90-day minimums.
- **WAL compression enabled.** WAL segments are a fixed 16MB regardless of
  content, so an idle database still emits padded full-size files. Compression is
  what keeps volume and cost proportional to actual change.
- **`archive_timeout` starts at 15 minutes.** This is the recovery-point dial,
  not a schedule: each tick forces a segment switch and ships a padded 16MB file.
  15 minutes cuts segment volume roughly two-thirds versus a 5-minute setting and
  reduces write wear on the USB-attached NVMe. It is free to go longer if AWS
  cost or load on the Pi call for it — the shape that matters is continuous
  archiving with PITR, not a particular number.
- **Dedicated IAM user** scoped to the single bucket prefix, no wildcards, so a
  compromised node has a blast radius of one prefix.
- **Bucket versioning and server-side encryption on**, so a bug or errant delete
  cannot irrecoverably erase backup history.

Backblaze B2 is ~70% cheaper per GB but was rejected: the saving is under a
dollar a month and does not justify a second vendor, credential set and renewal
surface.

The bucket, the IAM user and the budget alert below are not reconciled by
anything in this repo. Like the UniFi and Cloudflare configuration, they belong
in `docs/host-state.md` with everything else that fails silently.

### Retention

- **14 days, configured as the Barman `retentionPolicy`** — not as an S3
  lifecycle rule. Barman tracks which WAL segments the oldest base backup still
  requires. A lifecycle rule deleting by age has no such knowledge and can expire
  required WAL, producing a backup set that looks healthy in the console and
  fails at restore time.
- An S3 lifecycle rule at ~60 days is a runaway-cost backstop only.

Beyond two weeks there is no value in point-in-time granularity. There is no
long-horizon retention (monthly or yearly copies) of the database, and nothing in
this repo provides one for anything else either — see #352.

### Cost control

Expected cost is roughly $0.12–$1.50/month depending on compression
effectiveness. The ceiling is enforced, not hoped for:

- **An AWS Budget alert** on the account, so a runaway — an archive loop, a
  forgotten throwaway restore — is noticed in days rather than on the invoice.
- **The lifecycle backstop** above, so nothing grows without bound even if
  Barman's retention stops running.

If cost has to come down, the levers in order of least harm are
`archive_timeout` (longer means a worse recovery point), base backup frequency
(less often means longer restores), then retention (shorter means less
point-in-time reach). Each one changes the tenant contract, so the contract table
changes with it.

### Restore drills

Untested backups are not backups. A restore to a timestamp, into a throwaway
`Cluster`, with the target confirmed honoured, is performed:

- first while the database is still empty, before any tenant is onboarded;
- then quarterly;
- and after any change to backup or archive configuration.

PITR restores the whole instance, so restoring one tenant means restoring into a
throwaway `Cluster` and dumping that tenant's database back into the live one.
The runbook covers both.

### Resources: DNS wins

Postgres carries explicit memory requests and limits. The limit covers the
process, `shared_buffers`, *and* the hot part of the OS page cache: Postgres
relies on the page cache by design, and page cache is charged to the container's
limit. A limit sized to the process alone produces the self-eviction thrash
recorded in #378. Size from `memory.stat` under representative load, and
recompute the namespace `ResourceQuota` in the same change.

The DNS workload is given `requests == limits` (Guaranteed QoS) and a higher
`PriorityClass` than the database and than any tenant. Under memory pressure the
kubelet then evicts task automation rather than household DNS. This protects
other workloads *from* the database; it is the same reason Akron is the only site
that stores observability data.

### Alerting

Three alerts, and they are this repo's, not a tenant's — they describe the
platform's health, and the first one takes DNS down with it:

- **WAL archive failure.** Postgres will not recycle WAL that has not been
  successfully archived, so a silently failing archive fills the NVMe and takes
  down everything on the node. This is the highest-severity alarm in the stack
  and needs an alert, not a dashboard panel.
- **PVC free space.**
- **Backup age** — last successful base backup older than N hours.

### Major upgrades are a migration, not an upgrade in place

The database is not upgraded under its tenants. A new instance is made available
and tenants move to it, accepting a downtime window measured in minutes:

1. Stand up a test instance on the new major, restored from a backup of the
   current one, and deploy a throwaway copy of each tenant against it to confirm
   compatibility.
2. Tear them down.
3. Stand up the production instance on the new major, disconnect clients from the
   old one, take a final backup and restore it.
4. Point each tenant at the new instance — a change to its `<tenant>-database`
   Secret in this repo, and nothing in the tenant.

CNPG expresses steps 1 and 3 declaratively: a `Cluster` with
`bootstrap.initdb.import` runs `pg_dump`/`pg_restore` from the source cluster, so
the migration is a manifest rather than a typed procedure. The `monolith` import
type carries several databases and their roles at once, which is what a shared
instance needs; confirm it against the CNPG release in use when the first major
upgrade is due.

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
  one mechanism and one configuration, with nothing extra running on the Pi.
- Restore is a declarative, rehearsable object rather than an undocumented
  runbook.
- The database's manifests are ordinary components here, so they get the full
  validation pipeline, SOPS enforcement and dependency coverage. Had they lived
  in a tenant's repo and been pulled in at reconcile time, none of that would
  have applied — to the workload where it matters most.
- No additional infrastructure components: no Redis, no self-hosted object store.
  MinIO would have pre-allocated 1Gi per node in single-node topologies (2Gi
  distributed), against a design centre its own guidance puts at 8+ cores and
  128GB RAM. Garage would have been the choice had local S3 been necessary.
- A cutover to a new major, or to a database outside the cluster entirely, is a
  change here and nowhere else. The contract is already the one a managed
  service would offer.

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
- **Tenants share an instance.** A noisy tenant affects the others, a restart
  restarts all of them, and a whole-instance PITR rolls all of them back — hence
  the per-tenant restore path above. With one tenant, none of this bites yet.
- **This repo now owns an application's runtime dependency.** A tenant feature
  needing `pg_trgm` or a non-default server parameter is a PR here first. That
  lead time is the cost of the boundary.
- **Two hand-managed passwords per tenant**, each referenced twice — by the
  `Cluster` and by the tenant's Secret. If the two drift, the tenant fails to authenticate.
- **Version drift is caught late.** Nothing continuously checks that a tenant's
  pinned major matches what is served; the upgrade drill is the check.

## Alternatives considered

| Option | Rejected because |
| --- | --- |
| Plain StatefulSet with hand-written CronJobs | Fewest components, but hand-rolled WAL archiving and an untested manual restore path is the usual way homelab PITR turns out to be broken |
| An instance per tenant | A second set of memory, backups and alerts per tenant on a node where memory is the scarce resource, for isolation this data does not need |
| The `Cluster` and its configuration held in the tenant's repo | Couples the database's configuration to an application's release cycle, and its manifests would bypass every validation step here. Rejected in timbot's ADR 0003 |
| The `Cluster` in `infrastructure-config` | Where the CRD split alone would put it, but it shares a pruning `Kustomization` with unrelated configuration |
| Tenant colocated in the database's namespace, reading CNPG's generated Secret | Simpler, but makes the tenant's configuration name a particular `Cluster`, so a major-version cutover becomes a tenant change |
| A controller replicating the generated Secret across namespaces | A component to install, run and upgrade, to deliver a value a SOPS file already holds |
| Postgres on the host, outside K3s | Contradicts GitOps as the single source of truth, and puts the database outside validation entirely |
| Local PITR with a periodic (e.g. weekly) offsite copy | Needs a local object store or second disk running on the Pi, and the local copy shares the node's failure domain. Continuous S3 costs fewer resources and gives a better recovery point |
| Local S3 (MinIO / Garage) + rclone to Drive | Unnecessary once an existing AWS account is available; adds RAM, a component, and a shared failure domain |
| Google Drive / iCloud as the backup target | iCloud via rclone needs the real Apple ID password plus 2FA, produces a 30-day trust token requiring interactive reauth, and demands Advanced Data Protection off — a chain that silently dies monthly. Drive lacks object-store semantics and is the wrong shape for a continuous WAL stream |
| Replication or a second instance for HA | There is one node. It protects against nothing |

## Changelog

| Date | By | Description |
| --- | --- | --- |
| 2026-10-02 | Tim and Claude | Accepted as a platform service with a tenant contract; each tenant gets a database with read-write and read-only roles (#355) |
