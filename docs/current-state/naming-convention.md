# Naming Convention

How things are named here, in two parts: **DNS names** for anything reachable on
the network, and **Kubernetes object names** for everything inside a cluster.
Written down because `akron` used to mean four different things — UniFi site,
physical server, K3s node, and DNS label — with nothing to keep them apart.

**DNS names are live.** Iteration 4 of #228 cut every deployed name over from
`home.arpa` in one PR, together with TLS. Anything still on `home.arpa` is a
planning document recording past work, not a name in service.

**Kubernetes object names are still the target state.** Most existing object
names predate them and the DNS cutover did not touch them. Apply them to new
objects and to anything you are already editing; do not mass-rename to match.

## DNS names

```
<role>.<site>.internal.zerpzorp.com
```

**Site identifier** is one of `akron`, `eastbank`, `lottage`, used *verbatim*
everywhere it appears: the UniFi site name, `sites/<site>/`,
`clusters/<site>/`, the DNS label, and `SITE_NAME` in `cluster-vars.yaml`.
A site identifier never appears without a role in front of it, except as a
site index (below).

**Role** disambiguates. `akron` is always the site; the thing at that site
carries its own label.

| Thing | FQDN | Notes |
|---|---|---|
| UDR | `udr.akron.internal.zerpzorp.com` | forward *and* reverse asserted in dnsmasq; see below |
| Server / K3s node | `k3s01.akron.internal.zerpzorp.com` | Linux hostname *and* K3s node name |
| Apps | `pihole.akron.internal.zerpzorp.com` | via Traefik `IngressRoute` |
| | `grafana.akron.internal.zerpzorp.com` | |
| Telemetry ingress | `metrics.akron.internal.zerpzorp.com` | remote-write only; path-scoped |
| | `logs.akron.internal.zerpzorp.com` | push only; path-scoped |
| Non-Traefik Services | `syslog.akron.internal.zerpzorp.com` | its own MetalLB IP — see below |
| Site index | `akron.internal.zerpzorp.com` | zone apex → that site's Traefik |
| Global index | `internal.zerpzorp.com` → local Traefik | |
| Site-local | `pihole.internal.zerpzorp.com` | resolves to whichever site you are in |

### Names that are not behind Traefik

`*.<site>.internal.zerpzorp.com` resolves to that site's Traefik by wildcard,
so an app needs an `IngressRoute` and no DNS record at all. Anything *not*
behind Traefik needs an explicit override, or the wildcard swallows it and SSH
lands on the ingress controller.

The ones every site has (the node, the gateway and the syslog address) are in
`base/dns/dnsmasq-base.conf`, because the records are entirely variables
(`${NODE_IP}`, `${LAN_GATEWAY}`, `${METALLB_SYSLOG_IP}`) and a new site gets them
by defining those. Another site's syslog name needs a record in each
`site.conf`, because that site's subtree is forwarded to its Traefik by
wildcard; the addresses are `AKRON_SYSLOG_IP` and `EASTBANK_SYSLOG_IP`.
Anything else, such as a Service with its own MetalLB IP, is site-specific and
goes in `sites/<site>/infrastructure/site.conf`.

**The UDR cannot be renamed on the device.** UniFi OS answers its own reverse
lookup with the built-in short name `unifi`, and PiHole conditionally forwards
this LAN's reverse zone to the gateway, so `dig -x ${LAN_GATEWAY}` returned
`unifi.<site>.internal.zerpzorp.com` regardless of the name typed into the
console. Settings → Console → Name is a label for the UI and Site Manager and
never reaches the gateway's DNS; `ubios-udapi-server` owns `/etc/hostname`,
`/etc/hosts` and `/etc/resolv.conf` and regenerates all three, so an SSH
`hostname` change does not survive either.

So the UDR's record is `host-record=` rather than `address=`, which answers
both the A and the PTR locally. dnsmasq answers from local records before
forwarding, so it overrides that one address and every other DHCP client in
the range still resolves by its lease name from the gateway. The name is repo
state, not UniFi state — nothing to re-enter after a UniFi rebuild.

**One internal name is enough for the UDR's certificate.** UniFi OS validates
its Let's Encrypt order over DNS-01 with a Cloudflare API token, so no name
here needs to resolve publicly and no device needs a second, public label —
every name in this document stays internal. See
[Let's Encrypt Certificates on a UniFi Console](../runbooks/unifi-tls.md).

### Site-local names

Each site's PiHole is authoritative for the whole `internal.zerpzorp.com`
tree and answers the *unqualified* names with its own Traefik IP. More
specific `address=` lines override for cross-site names. Same name typed
anywhere, site-local answer, one config line — no anycast, no BGP.

### Typing burden

Solved by the landing pages, and **not** by the DHCP search domain. Not by
shortening the domain either.

The search domain (UniFi per-network "Domain Name", set to
`<site>.internal.zerpzorp.com`) does work, and does what it claims — it expands
a single label to the site FQDN before the query goes out:

```
$ ping pihole
PING pihole.akron.internal.zerpzorp.com (10.6.1.80)
```

**But a browser validates the certificate against the name you typed, not the
name it resolved to.** So `https://pihole/` fails, permanently:

```
$ curl -sSI https://pihole/
curl: (60) SSL: no alternative certificate subject name matches target host name 'pihole'
```

`*.akron.internal.zerpzorp.com` matches one label *under that domain* and
cannot match a bare `pihole`, and no public CA will ever close the gap — a
single-label name is not a public DNS name, so Let's Encrypt cannot issue for
it however the certificate is requested.

Nor can a redirect rescue it. Traefik's `web` → `websecure` redirect preserves
the Host header, so `http://pihole/` becomes `https://pihole/` and lands on the
same error. A host-*rewriting* redirect would work only for clients that try
plaintext first, which browsers increasingly do not.

So the search domain is for `ssh`, `ping`, `dig` and anything else without TLS,
where it is genuinely useful. For a browser, use either full name — both are on
the wildcard:

| | |
|---|---|
| `pihole.akron.internal.zerpzorp.com` | this site explicitly |
| `pihole.internal.zerpzorp.com` | [site-local](#site-local-names) — shorter, and resolves to whichever site you are standing in |

The second is the short name worth learning. It is not a special case; it is the
site-local scheme above, and it is why the wildcard certificate carries
`*.internal.zerpzorp.com` as well as the per-site one.

**MetalLB LoadBalancer IPs do not answer ICMP.** `ping pihole` resolves and then
times out, which looks like an outage and is not one — the VIP is not bound to
an interface on the node, so TCP is delivered and ICMP is not. Reach for `curl`,
never `ping`, when checking whether a service is up.

## Kubernetes object names

DNS names are only half of it. An in-cluster object name sits inside a namespace
and a kind, so most of the context a DNS name has to spell out is already free —
and the identity a name is often asked to carry belongs in Kubernetes'
[recommended labels](https://kubernetes.io/docs/concepts/overview/working-with-objects/common-labels/)
(`app.kubernetes.io/name`, `instance`, `component`, `part-of`, `managed-by`)
instead. Most naming pain is something smuggled into a name that should have been
a label.

**The name carries function. Everything else is a label.**

1. **Identity goes in `app.kubernetes.io/*` labels.** If you would want to select
   or group on it, it is a label, not a name fragment.
2. **The namespace is half the name.** `metallb-system/lan` needs no `metallb-`
   prefix — the namespace already said it.
3. **Never encode what can change** — host, site, environment, IP address, chart
   version. This is the rule `homelab-pool` broke: it was named for the box it
   happened to run on, and outlived it.
4. **Referenced names are contracts; unreferenced names are free.** A Service
   name becomes cluster DNS (`<svc>.<ns>.svc`), a ConfigMap name appears in a
   volume mount, an `IPAddressPool` name appears in
   `L2Advertisement.spec.ipAddressPools`. Those deserve thought. A Deployment
   name that nothing references does not.
5. **Qualify only to disambiguate siblings that coexist.** Two Services for one
   app need telling apart; a single Deployment does not.
6. **Hard constraint:** anything reachable by DNS must be a DNS-1123 label —
   lowercase alphanumeric and hyphens, 63 characters.

There is deliberately **no site prefix**. Every cluster is exactly one site, so
`akron-` inside Akron's cluster distinguishes nothing — it is the same
over-labelling that let `akron` mean four things.

### Worked example: the MetalLB pool

MetalLB has no naming convention of its own; upstream docs use placeholders
(`first-pool`, `example`, `cheap`/`expensive`), so the name is ours to choose.

The pool's name is referenced from `L2Advertisement`, making it a contract
(rule 4). It must not encode the site or host (rule 3). And it should describe
*which* address space, so that adding a second pool later does not force a
rename of the first:

```
IPAddressPool/lan
L2Advertisement/lan
```

`lan` is chosen over `default` or `primary` because the plausible second pools
are for a different kind of address space — a Cloudflare tunnel, or a VPN
segment — not a second-choice range of the same kind. Under `default`, the
second pool's arrival would make the first name a lie.

Same name across two kinds is deliberate, and preferable to `lan-pool` /
`lan-l2`: the kind is already printed beside the name, so a suffix restating it
is noise (rule 5 — there is no sibling to disambiguate from).

## Observability names

**Deployed in part.** Akron's ingest endpoints, their variables, each site's
collector push hostnames, the forwarder's release, volume claim and ConfigMap,
the node log reader, the `pod-logs`, `unifi-os-log` and `unifi-events`
streams, the syslog address at every site, and `network_site` on Unpoller's
signals are live. Everything else here
is a target name that #514 and #515 apply, and this project is the deliberate
exception to "do not mass-rename". Each name
says what the thing does, not which vendor runs it, and has to stay true with a
second collector, a fourth site or a different backend. The collector's
published contract is in [ADR 0009](../adr/0009-observability-ingest-contract.md).

### Labels

`site` and `job` are on every signal. `host` and `node` say which machine or
cluster node a log came from, where that applies.

| Label | Meaning | Set by |
| --- | --- | --- |
| `site` | The site whose ingest point received the signal | the collector, never the sender |
| `job` | The stream (below) | the collector |
| `host` | The machine that produced a log, by its network name: an off-cluster device, or a node for its own journal | the collector, from the syslog hostname or the connection address; the node log reader, from the journal |
| `node` | The Kubernetes node a pod's logs came from | the node log reader, from pod discovery |

- **`node` and `host` are different facts that share a value here.** A node is
  a cluster object and a host is a machine on the network. This repo names the
  K3s node after the Linux hostname ([DNS names](#dns-names)), so today both
  read `k3s01.<site>.internal.zerpzorp.com`, but either can change without the
  other. Pod logs carry `node`, because they come from the cluster. The journal
  carries `host`, because it describes the machine.
- **Kubernetes metrics carry the node as `instance`** (node-exporter, kubelet,
  cAdvisor), not `node`. That's Prometheus's convention, and the dashboards
  select on it.

- **`site` keeps its name and its meaning**, so dashboards and alerts that
  filter on it keep working. For a mobile sender it's the site the sender was
  at. Lottage's UniFi equipment, which has no collector at its own site, is
  stamped `lottage` by the Lottage-only ingest points it sends to (ADR 0009).
- **The network the data *describes* is a separate fact, `network_site`.**
  Unpoller at Eastbank polling Akron's controller sends `site="eastbank"`,
  `network_site="akron"`. Unpoller's `site_name` is renamed to `network_site`
  at collection, metrics and logs alike, and the UniFi dashboards select on
  it. Data from before the rename carries only `site_name`, so the
  dashboards show nothing for it. UniFi syslog needs no `network_site`: each
  console sends to its own site's ingest point, or to a relayed one stamped
  with its site, so `site` is already the network. CEF's old `Site` label was
  the console's device name, so it is `host` now, like every syslog line.
- **Values are lowercase**, and the collector lowercases values that arrive
  otherwise (Unpoller's `Akron` becomes `akron`).

### Accepted values

A reference for planning transformations, not a filter. A value that isn't
listed gets checked against this convention and added here.

| Label | Values |
| --- | --- |
| `site` | `akron`, `eastbank`, `lottage` |
| `network_site` | `akron`, `eastbank`, `lottage` |

**Unpoller's WAN metrics report `Default (default)`.** Since Unpoller v5
(2026-10-21), every `unpoller_wan_*` series carries `site_name="Default
(default)"` for every controller, while its device metrics carry the real
site. Before v5 those series had no `site_name` at all, and Unpoller's Protect
logs still have none. The collector sets `network_site` on all of them from
`source`, the controller URL, which identifies the site. That map is in
`sites/eastbank/monitoring/unpoller/unpoller.alloy`, beside Unpoller's own
configuration, and a new controller needs a rule there.

### Endpoints and variables

| Thing | Name | Replaces |
| --- | --- | --- |
| Akron's metrics ingest | `metrics.akron.internal.zerpzorp.com` | `prometheus.akron…` |
| Akron's logs ingest | `logs.akron.internal.zerpzorp.com` | `loki.akron…` |
| Each site's collector push hostname (OTLP, Loki push) | `collector.<site>.internal.zerpzorp.com` | — (new) |
| The same, [site-local](#site-local-names), for mobile senders | `collector.internal.zerpzorp.com` | — (new; no record needed, the per-site answer and the certificate already cover it) |
| Each site's syslog address | `syslog.<site>.internal.zerpzorp.com`, `METALLB_SYSLOG_IP`; another site's is `<SITE>_SYSLOG_IP` | Akron's only `syslog.akron…` |
| The same, [site-local](#site-local-names) | `syslog.internal.zerpzorp.com` | — (new; unlike `collector.`, it needs a record, because the wildcard's answer is Traefik) |
| Variables for the ingest URLs | `METRICS_INGEST_URL`, `LOGS_INGEST_URL` | `PROMETHEUS_REMOTE_WRITE_URL`, `LOKI_PUSH_URL` |

`collector.` is what senders push to, and `metrics.` and `logs.` are what
collectors write to. Keeping the two apart stops a sender from skipping the
collector by accident. `syslog.` names a protocol, and survived the last
backend swap (#151). What arrives there is syslog-framed UDP, though UniFi is
loose about it: the payloads range from standard syslog lines to embedded
application logs and kernel debug dumps, and CEF is a format carried inside
the same framing. It's also the name of the field every device asks for:
"syslog server".

### Streams (`job`)

| Stream | Name | Replaces |
| --- | --- | --- |
| UniFi OS system log | `unifi-os-log` | `unifi-siem` |
| UniFi events (CEF) | `unifi-events` | `unifi-cef` |
| OBi100 syslog | `obihai-syslog` | — (new) |
| Pod logs | `pod-logs` | `loki.source.kubernetes.pods` |
| Node journal | `systemd-journal` | unchanged |

An old name is never reused for the other stream (#411). Queries that span the
cutover use `job=~"old|new"` until the old data ages out.

### Metrics

Metrics this estate defines follow [Prometheus's naming
practice](https://prometheus.io/docs/practices/naming/): base units as a
suffix (`_seconds`, `_bytes`), `_total` on counters, and one number per series,
with every other fact a label. Third-party metrics keep their own names, apart
from the standardisation in [ADR 0008](../adr/0008-what-observability-collects.md).

- **`zerpzorp_` prefixes every metric defined here**, whether derived by a rule
  or emitted by our own software, so they are told apart from third-party
  series and selected with one regex.
- **Then the domain**: what the metric is about, such as `deployment`.
- **An event's series has the event's own time as its value**:
  `zerpzorp_<domain>_timestamp_seconds{event="…"}`, like
  `node_boot_time_seconds`. A Grafana annotation drawn from it lands at the
  moment of the event, whatever the evaluation cadence.
- **An event that has no key of its own is keyed by its time.** A deployment
  is one commit, but a link can drop twice in a minute, so each occurrence of
  a log-derived event without a natural key is its own series, labelled
  `occurrence` with its time in unix seconds. An event derived from a gauge
  that already holds the latest time, such as a boot, is one series per
  machine, and successive events are successive values.
- **A recording rule that defines a metric records that metric's own name.**
  A rule that aggregates one uses Prometheus's `level:metric:operations`.
- **An exception** is recorded beside the code that makes it, with its reason,
  and listed here.

| Metric | Labels | Defined in |
| --- | --- | --- |
| `zerpzorp_deployment_timestamp_seconds` | `site`, `revision`, `sha`, `event` = `started` | `sites/akron/monitoring/loki-rules/deployment.yaml` |
| | `event` = `finished`, `failed` | `additionalPrometheusRulesMap` in `sites/akron/monitoring/kube-prometheus-stack.yaml` |
| `zerpzorp_deployment_kustomization_timestamp_seconds` | `site`, `name`, `revision`, `sha`, `event` = `applied`, `failed` | `sites/akron/monitoring/loki-rules/deployment.yaml` |
| `zerpzorp_deployment_duration_seconds` | `site`, `revision`, `sha` | `additionalPrometheusRulesMap` |
| `zerpzorp_deployment_kustomizations` | `site`, `revision`, `sha`, `state` = `expected`, `applied`, `failed` | `additionalPrometheusRulesMap` |
| `zerpzorp_host_timestamp_seconds` | `site`, `host`, `event` = `booted`, `booted_unrequested` | `additionalPrometheusRulesMap` |
| | `site`, `host`, `action`, `reason_given`, `occurrence`, `event` = `shutdown_requested` | `sites/akron/monitoring/loki-rules/host.yaml` |
| | `site`, `host`, `device`, `occurrence`, `event` = `link_down`, `link_up`, `address_lost`, `address_acquired` | `sites/akron/monitoring/loki-rules/host.yaml` |
| `zerpzorp_host_reboot_duration_seconds` | `site`, `host` | `additionalPrometheusRulesMap` |
| `zerpzorp_host_address_outage_duration_seconds`, `zerpzorp_host_link_outage_duration_seconds` | `site`, `host`, `device` | `additionalPrometheusRulesMap` |
| `zerpzorp_gateway_timestamp_seconds` | `site`, `network_site`, `event` = `booted`, `booted_unrequested` | `additionalPrometheusRulesMap` |
| | `site`, `network_site`, `host`, `cause`, `occurrence`, `event` = `reboot_requested` | `sites/akron/monitoring/loki-rules/gateway.yaml` |
| | `site`, `network_site`, `version`, `previous_version`, `occurrence`, `event` = `firmware_updated` | `sites/akron/monitoring/loki-rules/gateway.yaml` |
| `zerpzorp_gateway_reboot_duration_seconds` | `site`, `network_site` | `additionalPrometheusRulesMap` |
| `zerpzorp_wan_timestamp_seconds` | `site`, `network_site`, `wan`, `occurrence`, `event` = `failed`, `restored` | `sites/akron/monitoring/loki-rules/wan.yaml` |

`revision` is Flux's own, `<branch>@sha1:<sha>`, the same key as
`gotk_resource_info`'s. `sha` is the revision without its branch, so a
commit can be followed across sites.

There are no exceptions yet.

### Behind the contract

These are free to change with the collector's shape.

| Thing | Name | Replaces |
| --- | --- | --- |
| The forwarder | HelmRelease `collector`; volume claim template `wal` (PVC `wal-collector-0`) | `alloy-metrics`, `alloy-data-alloy-metrics-0` |
| Its configuration | ConfigMap `collector-config` | `alloy-metrics-config` |
| The node log reader | HelmRelease `node-log-reader` | `alloy` (Akron only) |

Renaming the forwarder drops its PVC and its WAL, and changing an ingest URL
orphans the remote-write queue held for the old one (#306). Make both in one
change, so the WAL is lost once (#522).

## Why `internal.`, not the apex

A DNS-01 ACME token must be able to write to whatever zone holds
`_acme-challenge`. That token lives in a K3s secret on a Raspberry Pi — the
very thing being secured. Scoped to `internal.zerpzorp.com`, a compromised
cluster can only forge names that are already reachable only from inside the
network. Scoped to the apex, it could issue a cert for `www.zerpzorp.com` and
MITM future public endpoints.

Secondary: a wildcard on the apex would blackhole future public subdomains
for internal clients, Certificate Transparency logs every issued name, and
`internal.` gives one label to hang Unbound `private-domain`, firewall rules
and future auth policy on.

<!-- ponytail: today `internal.` is records inside the zerpzorp.com Cloudflare
zone with a zone-wide token, not a separately delegated zone. The names above
are unaffected either way — splitting the zone later is a record move plus a
token swap. #314 inventories every credential and doc that assumes the current
shape. -->

## Reserved: workload identity (not built yet)

Future mTLS uses **SPIFFE URI SANs** issued by a private cert-manager CA:

```
spiffe://zerpzorp.com/site/<site>/ns/<namespace>/sa/<serviceaccount>
```

e.g. `spiffe://zerpzorp.com/site/eastbank/ns/monitoring/sa/collector`.

URI SANs and DNS SANs are disjoint namespaces, so a private CA can never
collide with or shadow a Let's Encrypt name. Nothing needs reserving in DNS —
this only has to be written down before the first client cert exists, which
is what this section is.
