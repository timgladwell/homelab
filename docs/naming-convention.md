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
| Telemetry ingress | `prometheus.akron.internal.zerpzorp.com` | remote-write only; path-scoped |
| | `loki.akron.internal.zerpzorp.com` | push only; path-scoped |
| Non-Traefik Services | `syslog.akron.internal.zerpzorp.com` | its own MetalLB IP — see below |
| Site index | `akron.internal.zerpzorp.com` | zone apex → that site's Traefik |
| Global index | `internal.zerpzorp.com` → local Traefik | |
| Site-local | `pihole.internal.zerpzorp.com` | resolves to whichever site you are in |

### Names that are not behind Traefik

`*.<site>.internal.zerpzorp.com` resolves to that site's Traefik by wildcard,
so an app needs an `IngressRoute` and no DNS record at all. Anything *not*
behind Traefik needs an explicit override, or the wildcard swallows it and SSH
lands on the ingress controller.

The two every site has — the node and the gateway — are in
`base/dns/dnsmasq-base.conf`, because both records are entirely variables
(`${NODE_IP}`, `${LAN_GATEWAY}`) and a new site gets them by defining those.
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
[Let's Encrypt Certificates on a UniFi Console](runbooks/unifi-tls.md).

### Site-local names

Each site's PiHole is authoritative for the whole `internal.zerpzorp.com`
tree and answers the *unqualified* names with its own Traefik IP. More
specific `address=` lines override for cross-site names. Same name typed
anywhere, site-local answer, one config line — no anycast, no BGP.

### Typing burden

Solved by the DHCP search domain (UniFi per-network "Domain Name" set to
`<site>.internal.zerpzorp.com`) plus the landing pages. Not by shortening
the domain.

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

e.g. `spiffe://zerpzorp.com/site/eastbank/ns/monitoring/sa/alloy-metrics`.

URI SANs and DNS SANs are disjoint namespaces, so a private CA can never
collide with or shadow a Let's Encrypt name. Nothing needs reserving in DNS —
this only has to be written down before the first client cert exists, which
is what this section is.
