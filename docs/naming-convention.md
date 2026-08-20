# Naming Convention

Every internal name in this homelab follows one pattern. Written down because
`akron` used to mean four different things — UniFi site, physical server, K3s
node, and DNS label — with nothing to keep them apart.

## The pattern

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
| UDR | `udr.akron.internal.zerpzorp.com` | UniFi device name is `akron-udr` |
| Server / K3s node | `k3s01.akron.internal.zerpzorp.com` | Linux hostname *and* K3s node name |
| Apps | `pihole.akron.internal.zerpzorp.com` | via Traefik `IngressRoute` |
| | `grafana.akron.internal.zerpzorp.com` | |
| Site index | `akron.internal.zerpzorp.com` | zone apex → that site's Traefik |
| Global index | `internal.zerpzorp.com` → local Traefik | |
| Site-local | `pihole.internal.zerpzorp.com` | resolves to whichever site you are in |

### Site-local names

Each site's PiHole is authoritative for the whole `internal.zerpzorp.com`
tree and answers the *unqualified* names with its own Traefik IP. More
specific `address=` lines override for cross-site names. Same name typed
anywhere, site-local answer, one config line — no anycast, no BGP.

### Typing burden

Solved by the DHCP search domain (UniFi per-network "Domain Name" set to
`<site>.internal.zerpzorp.com`) plus the landing pages. Not by shortening
the domain.

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
token swap. Tracked in #228 iteration 0. -->

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
