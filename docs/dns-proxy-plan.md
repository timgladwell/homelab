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

**Flux reconciliation caveat:** Flux enforces the Git-declared state on every reconcile cycle, so it would overwrite a runtime-patched ConfigMap back to `mode: pihole` within minutes. To prevent this, the state ConfigMap must carry the `kustomize.toolkit.fluxcd.io/reconcile: disabled` annotation. With this annotation, Flux creates the ConfigMap on first deploy (with the safe `pihole` default) and then leaves it alone — the proxy owns it at runtime. To force a reset to the default, delete the ConfigMap; Flux will recreate it on the next reconcile.

```yaml
metadata:
  annotations:
    kustomize.toolkit.fluxcd.io/reconcile: disabled
```

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
| Image | Built via GitHub Actions, pushed to GHCR | Cross-compiled for ARM64 on GitHub's x86 runners (fast); public GHCR requires no pull credentials; Renovate can manage version bumps. See [Image build strategy](#image-build-strategy) below. |
| MetalLB IP | `10.6.1.52` (within existing `10.6.1.10-10.6.1.99` pool) | Adjacent to PiHole's `.53`; memorable |

---

## Repository Layout

```
services/
  dns-proxy/
    main.go            # entry point: flags, start DNS + HTTP servers
    dns.go             # DNS proxy logic (miekg/dns)
    dns_test.go        # DNS forwarding tests (real miekg/dns server on :0)
    state.go           # read/write mode via ConfigMap (in-cluster K8s client)
    state_test.go      # ConfigMap read/write tests (client-go fake client)
    ui.go              # HTTP server for the switch UI
    ui_test.go         # HTTP handler tests (net/http/httptest)
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

## Image Build Strategy

K3s uses `containerd` and pulls images from a registry at deploy time — there is no local Docker daemon in the loop. The options in order of preference:

**1. GitHub Actions → GHCR (recommended)**
A workflow triggers on changes to `services/dns-proxy/`, cross-compiles for ARM64 on GitHub's x86 runners, and pushes `ghcr.io/timgladwell/dns-proxy:<tag>`. Public GHCR packages require no pull credentials on the cluster side. Renovate can watch the image tag in the Deployment for automated version bumps. This is the clean GitOps path where code changes flow through Git the same way manifest changes do.

```
.github/workflows/
  dns-proxy.yaml    # on: push paths: services/dns-proxy/** → docker buildx → ghcr.io push
```

**2. Local containerd import (bootstrap shortcut)**
Build on the RPi over SSH, import directly into K3s's containerd store, set `imagePullPolicy: Never`:

```bash
# on the RPi
docker build -t dns-proxy:v1 services/dns-proxy/
docker save dns-proxy:v1 | k3s ctr images import -
```

Valid for getting the proxy running before CI is wired up. Not sustainable: requires a manual rebuild and reimport for every code change, and the image is invisible to GitOps.

**3. In-cluster local registry**
Run a `registry:2` pod inside K3s. Adds a dependency that must be running before the proxy can deploy — creates a chicken-and-egg problem when things break. Not recommended.

---

## Testing

Each source file has a corresponding test file. All tests run in-process with no external dependencies.

### `ui_test.go` — HTTP handlers (`net/http/httptest`)

```go
TestGetMode(t)         // GET / returns 200 with current mode
TestPostModeSwitch(t)  // POST /mode {"mode":"bypass"} updates in-memory state
TestPostModeInvalid(t) // POST /mode with unknown mode returns 400
```

No mocking needed — instantiate the handler directly, call it with an `httptest.ResponseRecorder`.

### `state_test.go` — ConfigMap read/write (`client-go` fake client)

`k8s.io/client-go/kubernetes/fake` provides a fully in-memory API server:

```go
TestReadMode(t)             // reads "pihole" from ConfigMap .data.mode
TestWriteMode(t)            // patch changes mode; subsequent read returns new value
TestReadMissingConfigMap(t) // returns safe default ("pihole") when ConfigMap absent
```

No real cluster, no network.

### `dns_test.go` — DNS forwarding (real `miekg/dns` server on `:0`)

`miekg/dns` exposes a `Server` type usable in tests. Bind mock upstreams to `:0` (OS-assigned port), point the proxy at them:

```go
TestForwardsToPihole(t)   // query in pihole mode reaches mock pihole upstream
TestForwardsBypass(t)     // query in bypass mode reaches mock cloudflare upstream
TestUDPAndTCP(t)          // both protocols forwarded correctly
TestBypassFailover(t)     // first bypass upstream down → proxy tries second
```

These are lightweight integration tests — real DNS wire protocol, no network egress. The mock upstream returns a fixed A record. Run time: milliseconds.

---

## Verification Steps

1. `kustomize build infrastructure/homelab/dns/` succeeds
2. `./scripts/validate-k3s.sh` passes
3. After merge + Flux reconcile: `dig @10.6.1.52 google.com` returns a result; PiHole query log shows the hit
4. Flip to bypass in UI → `dig @10.6.1.52 google.com` still resolves; PiHole log shows no new hit
5. Scale PiHole to 0 (`kubectl -n dns scale deploy pihole --replicas=0`) → flip to bypass → DNS still works
6. Scale PiHole back → flip to pihole mode → DNS goes through PiHole again
7. Delete proxy pod → it restarts → mode is preserved (read back from ConfigMap)
