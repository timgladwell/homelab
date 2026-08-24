# Reaching PiHole When Traefik Is Not Routing

The normal way in is `https://pihole.<site>.internal.zerpzorp.com`, served by
Traefik. When Traefik is the thing that is broken, that name resolves, presents
a certificate, and fails — so these are the ways in that do not involve Traefik
at all.

Reach for the CLI first. It answers most questions outright, and the UI is only
worth tunnelling to for the query log and the dashboards.

There is no IP path — #303 removed port 80 from PiHole's `LoadBalancer`, which
carries DNS only. Why, and why it was not simply encrypted instead, is decided
in #304.

## Most of the time you do not need the UI

The `pihole` CLI is in the pod, and reaches FTL directly — no browser, no
port-forward, no ingress, and nothing to close afterwards. This is the fastest
route to almost everything, including the common case of switching blocking
off for a moment:

```bash
kubectl -n dns exec deploy/pihole -- pihole status
kubectl -n dns exec deploy/pihole -- pihole disable 5m   # auto-re-enables
kubectl -n dns exec deploy/pihole -- pihole enable
kubectl -n dns exec deploy/pihole -- pihole -q ads.example.com
kubectl -n dns exec deploy/pihole -- pihole -g           # rebuild gravity
```

For the DNS log, `-it` so you can interrupt it:

```bash
kubectl -n dns exec -it deploy/pihole -- pihole tail
```

`pihole disable` takes a duration and re-enables itself, which is what you
want in a hurry — a permanent disable is easy to forget about. Note it is
site-wide and applies to every client, same as the button in the UI.

## When you do need the UI

```bash
kubectl -n dns port-forward svc/pihole-web 8080:80
```

**Run this on the machine with the browser** — your workstation, not the Pi.
`kubectl port-forward` opens the listening socket wherever `kubectl` runs, and
tunnels through the API server from there; the servers are headless and have
no browser to open. Nothing needs to be installed on the node, and the site's
own network path is not involved.

Leave it running and open <http://localhost:8080/admin> — the admin UI is
under `/admin`, not at the root. Ctrl-C when finished.

**There is no password** — the UI is deliberately unauthenticated, so this
runbook needs no credential. See `base/dns/pihole-deployment.yaml` for why.

If port 8080 is already taken locally, pick another: `port-forward svc/pihole-web
9090:80` and browse to `localhost:9090`. The second number is the Service port
and must stay 80.

`svc/pihole-web` is a ClusterIP Service targeting the pod's 8080. `kubectl`
forwards through the API server, so nothing in this path touches Traefik,
MetalLB, the ingress certificate, or DNS resolution of any name.

## Why this works when other things do not

| Broken | `https://pihole.<site>…` | `exec … pihole` | `port-forward` |
|---|---|---|---|
| Traefik pod down / crashlooping | ✗ connection refused | ✓ | ✓ |
| Traefik routing misconfigured | ✗ 404 or wrong backend | ✓ | ✓ |
| Wildcard certificate expired or not issued | ✗ browser refuses | ✓ | ✓ (plain HTTP to localhost) |
| MetalLB not assigning the Traefik IP | ✗ nothing answers | ✓ | ✓ |
| PiHole's dnsmasq config broken (no name resolves) | ✗ | ✓ | ✓ |
| PiHole pod itself down | ✗ | ✗ — fix the pod, not the route | ✗ |
| K3s API server down | ✗ | ✗ — nothing in this repo helps | ✗ |

The last two rows are the honest limit: both routes reach PiHole *through*
Kubernetes, so Kubernetes and the pod have to be up. Everything between them
and you is bypassed. The two columns fail together for that reason — the CLI is
faster, not more resilient.

Note the fifth row, which is the reason this beats a second hostname pointing
straight at the `LoadBalancer`. A broken DNS config is one of the most likely
reasons to want the PiHole admin UI in a hurry, and it is exactly the case
where no name — including one pointing at PiHole itself — resolves.

## If `port-forward` itself fails

```
error: unable to forward port because pod is not running. Current status=Pending
```

The pod is not up; this runbook does not apply. Check it directly:

```bash
kubectl -n dns get pods
kubectl -n dns describe pod -l app=pihole
kubectl -n dns logs -l app=pihole --tail=50
```

If the pod is `Running` but the forward drops immediately, confirm the Service
still has an endpoint — a selector that matches nothing looks identical to a
network problem:

```bash
kubectl -n dns get endpoints pihole-web
```

## Checking whether Traefik is actually the problem

Worth doing before assuming, because a certificate failure and a routing
failure look the same from a browser:

```bash
kubectl -n traefik get pods
kubectl -n traefik logs deploy/traefik --tail=50
kubectl get certificate -A                    # Ready=True on internal-wildcard?
kubectl -n traefik get ingressroute,tlsstore
```

A `TLSStore` named `default` must exist in the `traefik` namespace and name
`internal-wildcard-tls`, or Traefik serves its own self-signed certificate —
which is a working handshake and a browser warning, not an outage, and is easy
to mistake for an expired certificate.
