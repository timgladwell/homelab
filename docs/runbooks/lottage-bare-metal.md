# Lottage: Bare-Metal PiHole, Kept In Step By Hand

Lottage is a 2GB Raspberry Pi 4. It cannot run k3s — the control plane alone is
~700MB–1GB resident before any workload — so it has no Flux, no cluster and no
entry in `sites/`. It runs PiHole, nginx and an Alloy collector under docker
compose instead, from `bare-metal/lottage/`.

The goal is not architectural parity. It is that a client standing at Lottage
sees what a client at Akron or Eastbank sees: the same blocklists, the same
names resolving to the same things, a valid certificate, and the same
quick-links page at the apex.

## How it stays in step

`deploy.sh` runs **here**, on a workstation with the repo, and pushes to the
Pi. Nothing on the Pi pulls from git, and the box holds no git credentials.

```sh
git pull
./bare-metal/lottage/deploy.sh
```

That renders, validates, rsyncs to `/opt/lottage/` and runs
`docker compose up -d` followed by the blocklist sync. It reads these files out
of the repo every time, so a change made for the clusters arrives here too:

| File | Used as |
|---|---|
| `base/dns/pihole-deployment.yaml` | the container `env` list becomes `pihole.env` |
| `base/dns/dnsmasq-base.conf` | mounted at `/etc/dnsmasq.d/` |
| `base/landing/index.html` | the landing page |
| `base/pihole-sync/{sync.py,pihole-config.yaml}` | the blocklist sync, run unmodified |
| `clusters/common/network-vars.yaml` | `BASE_DOMAIN` and the other sites' addresses |

Lottage's own values are `bare-metal/lottage/lottage.env` (the equivalent of a
site's `cluster-vars.yaml`), `site.conf` and `pihole-clients.yaml`.

**There is no drift detection.** A setting changed in Lottage's PiHole UI
survives until the next `deploy.sh`, and only the keys the render covers are
corrected even then. That is the cost of the manual model, and it is the reason
as much as possible is lifted from `base/` rather than restated.

## Two deliberate divergences

**No Unbound; PiHole forwards to `1.1.1.1`/`1.0.0.1`.** Lottage is on DSL, and
recursion is the expensive choice there: a cold name costs Unbound 2–4 upstream
queries walking root → TLD → authoritative, plus 2–4 more for the DNSSEC
`DS`/`DNSKEY` chain, each a full round trip. Forwarding costs one query into an
already-hot cache. Warm-cache steady state is identical, so the whole
difference lands on first-hit latency — which is what is felt, once per
third-party domain on every page.

`FTLCONF_dns_dnssec` stays off for the same reason: enabling it re-adds exactly
the chain fetches this removes, and Cloudflare validates and refuses bogus
answers upstream.

The cost, stated plainly: one operator sees every query from this site. Akron
and Eastbank resolve recursively, and no single upstream sees their whole
picture. That property is traded away at Lottage only.

**nginx, not Traefik.** `nginx.conf` is the routing table: `pihole.` proxies to
the PiHole container, both apexes serve the landing page, and `:80` redirects.
The host pairs match `base/traefik-routes/pihole-ingressroute.yaml` and
`base/landing/ingressroute.yaml`, so the names behave identically. There is no
dashboard — the landing page's Traefik link is tagged `data-k8s-only` and
`deploy.sh` strips it.

## First standup

1. **Docker.** `curl -fsSL https://get.docker.com | sh`, then
   `usermod -aG docker $USER`.
2. **Free port 53.** Debian's `systemd-resolved` stub holds `127.0.0.53:53`.
   PiHole binds the LAN address specifically, so the two coexist — but confirm
   with `ss -ulnp | grep :53` before and after.
3. **Deploy directory.** `sudo mkdir -p /opt/lottage && sudo chown $USER /opt/lottage`
   — `rsync` writes there as the login user, not as root.
4. **Cloudflare token.** `/etc/lottage/cloudflare.ini`, mode `600`, containing
   `dns_cloudflare_api_token = <token>`. The same token cert-manager uses. This
   is the only secret on the box and the only thing here not in git.
5. **First certificate.** `docker compose --profile certs run --rm certbot`.
   Issues `*.lottage.internal.zerpzorp.com`, `internal.zerpzorp.com` and
   `*.internal.zerpzorp.com` — the same three names, in the same shape and for
   the same reasons, as `base/cert-manager-config/certificate.yaml`.
6. **Deploy.** `./bare-metal/lottage/deploy.sh` from a workstation.
7. **Point clients at it.** UniFi → the Lottage network → DNS server, set to
   the Pi's address; and the per-network **Domain Name** to
   `lottage.internal.zerpzorp.com`, which must match `SITE_DOMAIN` or PiHole's
   conditional forwarding asks the gateway about a domain it does not know.

## Renewal

A systemd timer, weekly:

```sh
cd /opt/lottage && docker compose --profile certs run --rm certbot renew \
  && docker compose restart nginx
```

`certonly` registered a `--deploy-hook` that chowns the certificate tree to uid
101, because `nginx-unprivileged` runs as that user and certbot writes the key
`0600` as root. Renewal re-runs it.

Lottage is the third writer of `_acme-challenge.internal.zerpzorp.com`, after
Akron and Eastbank — see #314. Multiple TXT records at one challenge name is
ordinary DNS-01, and each solver removes only the value it added.

## What is monitored

One Alloy container remote-writes to Akron, with `site="lottage"`, over the
same 24h WAL every site uses. It runs two exporters, both built in:

- `prometheus.exporter.unix` — the machine. Lands in the existing
  `node-exporter-full` dashboard with nothing added at Akron.
- `prometheus.exporter.blackbox` — a DNS probe at the local PiHole. This is the
  question worth asking: a container can be up, healthy and resolving nothing.

Docker's own metrics endpoint is engine-level and answers neither. cAdvisor
would answer the per-container version at ~150–250MB, which is more than the
workloads it would watch — `prometheus.exporter.cadvisor` is available
in-process if that ever changes.

**Nothing alerts on any of it yet**: Alertmanager has no receivers or rules
(#214). Lottage inherits that gap rather than adding one.

There is no PiHole exporter here — and none at Akron or Eastbank either, so
query-rate metrics are absent estate-wide rather than missing at this site.
Pod-style log collection is likewise absent by design; `docker logs` on the box
is the equivalent, and it dies with the container.

## Verification

From a Lottage client:

```sh
dig +short pihole.lottage.internal.zerpzorp.com @<lottage-ip>   # the Pi
dig +short doubleclick.net @<lottage-ip>                        # 0.0.0.0
curl -Is https://pihole.lottage.internal.zerpzorp.com/admin/    # 200, valid cert
curl -Is https://internal.zerpzorp.com/                         # landing page
```

From an Akron client, the cross-site half:

```sh
dig +short pihole.lottage.internal.zerpzorp.com @10.6.1.53      # LOTTAGE_HOST_IP
```

In Grafana, `up{site="lottage"}` and the `node-exporter-full` dashboard with
`site=lottage` selected.
