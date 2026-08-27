# State That Is Not In Git

Everything in this repo is reconciled by Flux. This page is the inventory of
everything that **is not** — the settings that live on a node, in a UniFi
controller, or in a cloud account, which no reconcile will restore.

It exists because that set has grown, it is scattered across four runbooks and
a script, and every item in it fails **silently**. Nothing alerts when the
kubelet resolver config is missing; you find out when pods cannot resolve
anything, months later, during unrelated work.

**Rule of thumb:** if a rebuild would not recreate it, it belongs on this page.
When you add host-level configuration, add it here in the same PR.

---

## Per node

Set by `scripts/set-node-identity.sh` (idempotent — re-running converges):

| What | Where | Why |
|---|---|---|
| Static hostname | `hostnamectl` | K3s takes its node name from it. Must be the FQDN — see [naming convention](naming-convention.md). |
| Host mapping | `/etc/hosts` | Regenerated wholesale, `127.0.1.1` → FQDN. |
| cloud-init disabled | `/etc/cloud/cloud-init.disabled` | `update_etc_hosts` is `PER_ALWAYS` and rebuilds `/etc/hosts` from the *seed's* hostname every boot, silently reverting the rename. |
| Kubelet resolver | `/etc/rancher/k3s/resolv.conf` | The host's resolvers minus the `search` line. |
| Kubelet arg | `kubelet-arg: resolv-conf=…` in `/etc/rancher/k3s/config.yaml` | Without this the file above is inert. Stops pods inheriting the node's search domain, which otherwise breaks all external DNS in every pod — see [Trap 3](runbooks/node-rename.md). |

Set by hand, per [Standing Up a New Headless Box](runbooks/new-box-standup.md):

| What | Where | Why |
|---|---|---|
| Resolver list | `nmcli … ipv4.dns "<site-pihole-vip> 1.1.1.1 1.0.0.1"` | PiHole first, public fallback so Flux and `kubectl` can pull images when PiHole is down. **Not** `127.0.0.1` — PiHole is a MetalLB VIP, not `hostNetwork`. |
| DHCP override | `nmcli … ipv4.ignore-auto-dns yes` | Stops DHCP appending its own list on top. |
| Revoked sudo | `/etc/sudoers.d/90-cloud-init-users` | Cloud-init grants `NOPASSWD` for first login; it is meant to be removed. |
| Dotfiles | `~` | [dotfiles README](https://github.com/timgladwell/dotfiles#servers). |

Set during k3s install, per [Bootstrapping a New Remote Site](runbooks/bootstrap-new-remote-site.md):

| What | Where | Why |
|---|---|---|
| k3s install flags | `--disable traefik --disable servicelb`, `K3S_KUBECONFIG_MODE=644` | This repo deploys Traefik and MetalLB; the built-ins conflict. Without the kubeconfig mode, `kubectl` and `flux` fail as non-root. |
| cgroup flags | `cgroup_memory=1 cgroup_enable=memory` in `/boot/firmware/cmdline.txt` | Raspberry Pi OS does not enable the memory cgroup by default and k3s will not start without it. |

### Akron only

`/etc/rancher/k3s/config.yaml` also carries `kube-controller-manager-arg` and
`kube-scheduler-arg` set to `bind-address=0.0.0.0`, added so kube-prometheus-stack
could scrape those components over the node IP.

**These are vestigial.** PR #51 disabled both scrapers — on K3s these components
are embedded in the server binary and expose ~51K samples per scrape, of which
~724 series survive relabeling. The args are harmless but no longer serve
anything, and Eastbank does not have them. Do not copy them to a new node.

---

## In-cluster, but not in git

Created once at bootstrap and never reconciled. A cluster rebuild needs both
before Flux can do anything useful.

| What | How | Notes |
|---|---|---|
| `sops-age` Secret in `flux-system` | `scripts/configure-flux-sops.sh`, or `kubectl create secret generic` | The site's age private key. Without it every Kustomization with `decryption:` fails. |
| `flux-system` Secret | `flux bootstrap --token-auth` | Holds the GitHub PAT. Rotation: [runbook](runbooks/github-pat-rotation.md). |

---

## Off-cluster

| What | Where | Notes |
|---|---|---|
| DHCP DNS servers | UniFi, per network | All VLANs hand out the site PiHole only. The public fallback is per-node static config, deliberately — see below. |
| DHCP search domain | UniFi, per network | Set to `<site>.internal.zerpzorp.com`, which resolves for real since #233. Single-label lookups on those VLANs now land on the site's Traefik via the `${SITE_DOMAIN}` wildcard. |
| Syslog targets | UniFi → Settings → System → Remote Logging | Points at Akron's `alloy-syslog` LoadBalancer. **Still set to the old `home.arpa` name**, which stopped resolving with #303 — retyping it on the device is tracked in #234. |
| UniFi read-only user | Each controller | Consumed by Unpoller (via SOPS) and NetworkOptimizer (via its own UI). |
| Cloudflare zone, CAA, API tokens | Cloudflare + 1Password | `acme-akron` / `acme-eastbank`. CAA restricting to Let's Encrypt sits on `internal`, not the apex — Cloudflare's own five-CA set at the apex must stay for Universal SSL. |
| UDR web UI certificate | UniFi → Settings → Control Plane → Console | Let's Encrypt for `udr.<site>.internal.zerpzorp.com`, issued and renewed by UniFi itself over DNS-01. One `unifi-<site>-udr` Cloudflare token per console, pasted into the UI, and the certificate has to be **activated** after issuing. Renewal fails silently if the token is revoked, and nothing scrapes the console's certificate. Reissue: [runbook](runbooks/unifi-tls.md). |
| Grafana `claude-code` service account | Grafana UI → its PVC | Viewer-scoped, read-only query access for Claude Code. Lost with the Grafana PVC; symptom is queries failing 401. Reissue: [runbook](runbooks/grafana-query-access.md). |
| NetworkOptimizer UniFi credentials | Its web UI → SQLite on its PVC | **The only application state that no rebuild can restore.** Anything replacing that volume means re-entering them. |

### Why the resolver fallback is not in DHCP

#173 originally proposed a two-entry DHCP list (`<pihole>, 1.1.1.1`) on the
server VLAN. That is not what was built, and the difference is deliberate:
**a DHCP-distributed resolver list cannot express priority.**

- **Only Linux treats the list as ordered.** macOS, iOS and Windows clients
  round-robin or race the servers they are given, so any client handed a public
  resolver alongside PiHole will sometimes use the public one — silently
  bypassing filtering.
- **Even for Linux boxes, DHCP does not guarantee which server registers as
  primary.** Ordering is not something the protocol lets you rely on, so a
  Linux-only segment would not make the DHCP approach safe either.

So the fallback lives in each node's NetworkManager config with
`ipv4.ignore-auto-dns yes`, where the order is explicit and the scope is exactly
the machines that need it — the ones that must keep resolving when PiHole is
down in order to fix PiHole. Every client, on every VLAN, gets PiHole alone.

**Deferred, not solved.** This means the fallback is configured by hand per
node. That is fine at two nodes. If node count grows, or the list needs to
change often, this becomes the wrong shape and wants revisiting — most likely
as a resolver that is itself highly available, rather than as a longer list
handed to clients.

---

## What survives what

| Event | Lost |
|---|---|
| Pod restart | Nothing here. |
| `kubectl delete pvc` | Application state on that volume (PiHole gravity, Prometheus/Loki history, NetworkOptimizer's UniFi credentials). |
| Node rename | Nothing here — NetworkManager profiles bind to the interface, not the hostname. See [Renaming the K3s Node](runbooks/node-rename.md). |
| k3s reinstall | The in-cluster secrets above. Host files survive. |
| **Reflash** | **Everything in "Per node".** This is what [new-box-standup](runbooks/new-box-standup.md) exists to rebuild. |

## Container logs are not state, and not durable either

Worth knowing because it changes how you debug. The log-shipping Alloy
DaemonSet runs at **Akron only**; other sites run `alloy-metrics`, which ships
metrics and accepts pushes from applications, but does not collect pod logs.

So at a remote site a pod's logs die with the pod. `kubectl logs --previous`
reaches exactly one generation back, and a crashlooping pod destroys its own
evidence. Capture with `kubectl logs -f` while it happens, or lose it — this is
how the first generated NetworkOptimizer admin password became unrecoverable
during #230.
