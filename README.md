# Homelab Bootstrap

This repository contains the scripts, configuration files, and Kubernetes manifests required to bootstrap a containerized homelab from a bare-metal OS install on a Raspberry Pi 4B.

## Architecture Overview

The homelab consists of:
- **K3s**: Lightweight Kubernetes distribution optimized for edge devices
- **Flux CD**: GitOps continuous delivery tool
- **PiHole**: Network-wide ad blocking DNS server

## Prerequisites

- Raspberry Pi 4B with 8GB RAM, 256GB SSD
- Raspberry Pi OS Lite (64-bit) installed
- Network connectivity configured
- SSH access with sudo privileges

## Quick Start

### 1. Bootstrap K3s

Run the K3s installation script (requires root/sudo):

```bash
sudo ./bootstrap-k3s.sh
```

This script:
- Installs K3s using the official installation script
- Configures local-path storage provisioner (up to 100GB)
- Ensures K3s starts automatically on boot
- Is idempotent and safe to run multiple times

**Technical Decision**: Uses official K3s installer rather than manual installation for reliability and automatic service management. Local-path provisioner is included by default in K3s, providing persistent storage without external dependencies.

### 2. Configure kubectl

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
# Or add to your ~/.bashrc or ~/.zshrc
```

### 3. Create PiHole Secret

Before deploying PiHole, create the required secret with your network configuration:

```bash
export LOCAL_DOMAIN="yourdomain.local"
export GATEWAY_IP="192.168.1.1"
export SUBNET_MASK="255.255.0.0"
./scripts/create-pihole-secret.sh
```

**Security Note**: This secret contains network-specific information. Never commit actual secrets to the repository. The template file (`k8s/pihole/pihole-secret-template.yaml`) shows the schema with dummy values.

### 4. Bootstrap Flux CD

Install Flux CD CLI and configure GitOps:

```bash
# Install Homebrew if not already installed
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Run Flux bootstrap
./bootstrap-flux.sh
```

This script:
- Installs Flux CD CLI via Homebrew
- Bootstraps Flux CD into the K3s cluster
- Configures GitOps repository monitoring

**Technical Decision**: Using Flux CD's GitHub bootstrap for simplicity and automatic SSH key management. The repository at `https://github.com/timgladwell/homelab` will be monitored for changes.

### 5. Deploy Applications

Once Flux CD is configured, it will automatically sync manifests from the GitOps repository. The manifests in the `k8s/` directory define:

- PiHole namespace, PVC, ConfigMap, Deployment, and Service
- Flux CD GitRepository and Kustomization resources
- Post-deployment configuration jobs

## PiHole Configuration

### Default Configuration

- **Upstream DNS**: Cloudflare (1.1.1.1, 1.0.0.1)
- **DNSSEC**: Enabled
- **Web UI Password**: Disabled (no authentication)
- **Private Relays**: Disabled (iCloud private relay blocked)
- **DHCP**: Disabled (using router DHCP)
- **Timezone**: America/Toronto

### Network Access

PiHole is configured with `hostNetwork: true` to allow direct IP access for DNS queries. The container will be accessible via the Raspberry Pi's IP address on:
- Port 53 (UDP/TCP) for DNS
- Port 80 (TCP) for web UI

**Technical Decision**: Using hostNetwork mode instead of NodePort/LoadBalancer because:
- DNS requires direct IP accessibility on standard ports
- Simplifies client configuration (no need for port mapping)
- Reduces network overhead
- Trade-off: Only one PiHole instance per node (sufficient for single-node setup)

### Conditional Forwarding

Reverse DNS (conditional forwarding) is configured to forward queries for your local domain to your gateway using CSV format: `<enabled>,<cidr-ip-address-range>,<server-ip-address>,<domain>`. This requires setting:
- `LOCAL_DOMAIN_`: Your local domain (e.g., `home.local`)
- `GATEWAY_IP_`: Your router/gateway IP
- `SUBNET_MASK_`: Your subnet mask (e.g., `255.255.0.0` for /16)
- The script automatically calculates the CIDR range and generates the conditional forwarding CSV format

### Ad Lists

The adblock lists are configured in `k8s/pihole/configmap-adlists.yaml` ConfigMap, which is the single source of truth for adlist subscriptions:
- `pihole_allow_lists_request_body.json`: Whitelist entries (stored as ConfigMap key)
- `pihole_block_lists_request_body.json`: Blocklist subscriptions (stored as ConfigMap key)

The ConfigMap data is mounted into the post-deployment job and used to configure PiHole.

## Post-Deployment Configuration

The adlist configuration is handled by a Kubernetes Job that runs automatically in two scenarios:
1. **When PiHole is first deployed** - Flux CD will automatically create and run the post-deployment Job
2. **When adlist configuration is updated** - After updating the adlist entries in `k8s/pihole/configmap-adlists.yaml`:
   - Commit and push changes to trigger Flux CD sync
   - The Job will be recreated automatically by Flux CD

### Manual Trigger

To manually trigger the Job after updating the adlist ConfigMap:

```bash
# Option 1: Delete existing Job and let Flux CD recreate it (GitOps approach)
./scripts/trigger-pihole-adlist-update.sh

# Option 2: Apply ConfigMap and Job directly for immediate execution
./scripts/trigger-pihole-adlist-update.sh --apply
```

**Technical Decision**: Post-deployment configuration uses a single Kubernetes Job implementation that:
- Runs automatically via Flux CD when PiHole is deployed or when the adlist ConfigMap is updated
- Can be manually triggered using the helper script (`trigger-pihole-adlist-update.sh`)
- Uses an init container to wait for PiHole readiness before configuring adlists
- Mounts the adlist JSON files from a ConfigMap for version control
- Allows re-running configuration without pod restart
- Provides better error handling and retry logic compared to init containers or standalone scripts

## Directory Structure

```
.
├── k8s/                          # Kubernetes manifests
│   ├── namespace.yaml            # PiHole namespace
│   ├── pihole-pvc.yaml          # Persistent volume claim
│   ├── pihole-configmap.yaml    # Non-sensitive PiHole config
│   ├── pihole-secret-template.yaml  # Secret schema (dummy values)
│   ├── pihole-deployment.yaml   # PiHole container deployment
│   ├── pihole-service.yaml      # Service definition
│   ├── pihole-serviceaccount.yaml  # RBAC for post-deploy job
│   ├── configmap-adlists.yaml   # Adlist definitions
│   ├── pihole-post-deploy-job.yaml  # Post-deployment configuration
│   ├── flux-gitrepository.yaml  # Flux GitRepository resource
│   ├── flux-kustomization.yaml  # Flux Kustomization resource
│   └── kustomization.yaml       # Kustomize base configuration
├── scripts/
│   ├── create-pihole-secret.sh     # Secret creation helper
│   └── trigger-pihole-adlist-update.sh  # Trigger Job after ConfigMap updates
├── bootstrap-k3s.sh              # K3s installation script
├── bootstrap-flux.sh             # Flux CD installation script
└── README.md                     # This file
```

## Operational Scripts

All scripts are designed to be:
- **Idempotent**: Safe to run multiple times
- **Self-documenting**: Clear logging and error messages
- **Repeatable**: Can be run on fresh installations

### Bootstrap Scripts (One-time)

- `bootstrap-k3s.sh`: Installs and configures K3s
- `bootstrap-flux.sh`: Installs Flux CD CLI and bootstraps cluster

### Operational Scripts (Ongoing)

- `scripts/create-pihole-secret.sh`: Creates/updates PiHole secret
- `scripts/trigger-pihole-adlist-update.sh`: Triggers Job recreation after updating adlist ConfigMap

## Troubleshooting

### K3s not starting

```bash
sudo systemctl status k3s
sudo journalctl -u k3s -f
```

### PiHole pod not ready

```bash
kubectl get pods -n pihole
kubectl describe pod -n pihole -l app=pihole
kubectl logs -n pihole -l app=pihole
```

### Flux CD not syncing

```bash
flux get sources git
flux get kustomizations
flux logs -n flux-system
```

### Check PiHole DNS

```bash
dig @<PIHOLE_IP> example.com
```

## Security Considerations

1. **Secrets Management**: Never commit actual secrets. Use environment variables or secret management tools.
2. **Network Isolation**: Consider network policies for production use.
3. **Web UI Access**: PiHole web UI has no password by default. Consider restricting access via firewall rules.
4. **SSH Keys**: Flux CD bootstrap creates SSH keys automatically. Keep them secure.

## Storage Management

The local-path provisioner dynamically creates PersistentVolumes up to 100GB total. Each PVC will be allocated from this pool. Monitor usage:

```bash
kubectl get pv
df -h /var/lib/rancher/k3s/storage
```

## Maintenance

### Update PiHole

Update the image tag in `k8s/pihole/pihole-deployment.yaml` and commit to GitOps repository. Flux CD will automatically deploy the update.

### Update Ad Lists

When adlist configuration in `k8s/pihole/configmap-adlists.yaml` is updated:

1. **Commit and push**: Commit the updated ConfigMap to the GitOps repository
2. **Automatic sync**: Flux CD will detect the changes, update the ConfigMap, and recreate the Job automatically

Alternatively, use the helper script:
```bash
./scripts/trigger-pihole-adlist-update.sh
```

The post-deployment Job will run automatically when:
- PiHole is first deployed
- Adlist configuration in the ConfigMap is updated

### Backup PiHole Configuration

```bash
kubectl exec -n pihole -l app=pihole -- tar czf - /etc/pihole > pihole-backup-$(date +%Y%m%d).tar.gz
```

## GitOps Workflow

1. Make changes to manifests in `k8s/` directory
2. Commit and push to `https://github.com/timgladwell/homelab`
3. Flux CD detects changes and syncs automatically
4. Kubernetes applies changes to the cluster

## References

- [K3s Documentation](https://k3s.io)
- [Flux CD Documentation](https://fluxcd.io)
- [PiHole Documentation](https://docs.pi-hole.net)
- [PiHole API Documentation](https://discourse.pi-hole.net/t/pi-hole-api/1865)
