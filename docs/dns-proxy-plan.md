# DNS Proxy — Design Plan

## Context

PiHole runs in Kubernetes at MetalLB IP `10.6.1.53`. When PiHole breaks (pod crashloop, bad config, failed image pull, PVC issue), DNS goes down for the whole home network — blocking internet access and also blocking the tools needed to fix it (GitHub for Flux reconciliation, package registries, etc.). The cluster itself rarely goes down; it's PiHole that causes trouble.

The goal is a lightweight DNS proxy that sits in front of PiHole and can be flipped to bypass it, routing queries directly to Cloudflare instead. It lives in the cluster alongside PiHole and fits the existing GitOps model.

---

## Critical Design Notes

### Router reconfiguration is required (one-time)
Clients currently use `10.6.1.53` (PiHole's MetalLB IP) as their DNS server, set via DHCP on the UniFi Dream Machine. The router must be updated to hand out `10.6.1.52` (the proxy's MetalLB IP) instead. PiHole keeps `10.6.1.53` — the proxy just forwards to it.

### Both UDP and TCP are required for DNS
DNS uses UDP for most queries but falls back to TCP for responses > 512 bytes. The proxy must handle both protocols on port 53.

### Upstream redundancy in bypass mode
Use both `1.1.1.1` and `1.0.0.1`; fail over to the second if the first times out.

### State persistence across pod restarts
The mode switch (pihole/bypass) must survive pod restarts. A `ConfigMap` in the `dns` namespace works well: the proxy server patches it via the Kubernetes API when the switch is flipped.

### RBAC needed
The proxy pod needs a `ServiceAccount` with permission to `get`/`patch` its own `ConfigMap` to persist state.

---

## Architecture

```
Clients (DHCP DNS: 10.6.1.52)
    │
    ▼ port 53 UDP/TCP
dns-proxy pod (MetalLB: 10.6.1.52, in dns namespace)
    │
    ├─── [pihole mode]   ──► 10.6.1.53:53  (PiHole → Unbound → root servers)
    │
    └─── [bypass mode]  ──► 1.1.1.1:53 / 1.0.0.1:53  (Cloudflare)

    │ port 8080 → Traefik IngressRoute
    └─── Switch UI  (http://dns-proxy.homelab.home.arpa)
```

**DNS resolution chain in pihole mode (unchanged from today):**
Client → proxy → PiHole (`10.6.1.53`) → Unbound (`10.43.0.53:5335`) → root servers

---

## Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Language | **Go** | `miekg/dns` is the gold standard DNS library; single static binary; ARM64 cross-compile trivial; <10MB RSS |
| DNS library | `github.com/miekg/dns` | Battle-tested, handles UDP+TCP, well-documented proxy patterns |
| DNS bind | `0.0.0.0:53` UDP+TCP | Standard |
| UI bind | `0.0.0.0:8080` | Exposed via Traefik IngressRoute; avoids port 80 conflict |
| State | `ConfigMap` in `dns` namespace, patched by the proxy via the in-cluster K8s API | Survives pod restarts; fits GitOps; readable with `kubectl` |
| UI assets | `embed.FS` (Go embed) | Single binary, no external files |
| Logging | `log/slog` to stdout | Captured by existing Loki/Promtail log pipeline |
| Image | Built via `Dockerfile`, pushed to GHCR | Standard K8s workflow; Renovate can manage version bumps |
| MetalLB IP | `10.6.1.52` (within existing `10.6.1.10-10.6.1.99` pool) | Adjacent to PiHole's `.53`; memorable |

---

## Repository Layout

```
services/
  dns-proxy/
    main.go            # entry point: flags, start DNS + HTTP servers
    dns.go             # DNS proxy logic (miekg/dns)
    state.go           # read/write mode via ConfigMap (in-cluster K8s client)
    ui.go              # HTTP server for the switch UI
    static/
      index.html       # minimal switch UI (embedded via go:embed)
    Dockerfile         # multi-stage ARM64 build

infrastructure/homelab/dns/
  dns-proxy-deployment.yaml    # Deployment (dns namespace)
  dns-proxy-service.yaml       # LoadBalancer service (10.6.1.52, ports 53 UDP+TCP)
  dns-proxy-ui-service.yaml    # ClusterIP service (port 8080, for Traefik)
  dns-proxy-ingressroute.yaml  # Traefik IngressRoute → dns-proxy.${HOSTNAME}
  dns-proxy-configmap.yaml     # state ConfigMap (mode: pihole)
  dns-proxy-rbac.yaml          # ServiceAccount + Role + RoleBinding
  kustomization.yaml           # (existing file, add new resources here)
```

---

## UI Design

Minimal HTML page at `http://dns-proxy.homelab.home.arpa`:
- Prominently displays current mode (`PIHOLE` / `BYPASS`)
- Single button to toggle
- Last-switched timestamp
- No auth (home network only, acceptable for homelab)

The UI calls `POST /mode` with `{"mode": "pihole"}` or `{"mode": "bypass"}`. The Go server patches the `dns-proxy-state` ConfigMap via the in-cluster K8s client and updates its in-memory state immediately.

---

## Resource Limits

Sized conservatively for the RPi 4B:

```yaml
resources:
  requests:
    cpu: 10m
    memory: 20Mi
  limits:
    cpu: 100m
    memory: 64Mi
```

---

## One-Time Setup

1. Merge the implementation PR — Flux reconciles the deployment and MetalLB assigns `10.6.1.52`
2. On the UniFi Dream Machine: **Settings → Networks → [LAN] → DHCP Name Server** → change from `10.6.1.53` to `10.6.1.52`
3. Clients pick up the new DNS server on next DHCP renewal

PiHole's MetalLB IP (`10.6.1.53`) is unchanged.

---

## Future Revisions (Out of Scope for v1)

- Multiple upstream options in the switch (Google `8.8.8.8`, ISP resolver, etc.)
- Health-check auto-failover (detect PiHole is down and flip automatically)
- Metrics endpoint for Prometheus
- Bare-metal systemd fallback for full K3s outage resilience (deeper protection than in-cluster)

---

## Verification Steps

1. `kustomize build infrastructure/homelab/dns/` succeeds
2. `./scripts/validate-k3s.sh` passes
3. After merge + Flux reconcile: `dig @10.6.1.52 google.com` returns a result; PiHole query log shows the hit
4. Flip to bypass in UI → `dig @10.6.1.52 google.com` still resolves; PiHole log shows no new hit
5. Scale PiHole to 0 (`kubectl -n dns scale deploy pihole --replicas=0`) → flip to bypass → DNS still works
6. Scale PiHole back → flip to pihole mode → DNS goes through PiHole again
7. Delete proxy pod → it restarts → mode is preserved (read back from ConfigMap)
