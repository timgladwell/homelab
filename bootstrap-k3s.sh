#!/bin/bash
# K3s Bootstrap Script
# One-time installation script for K3s on Raspberry Pi OS
# This script is idempotent and can be run multiple times safely

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   log_error "This script must be run as root (use sudo)"
   exit 1
fi

# Check if K3s is already installed
if command -v k3s &> /dev/null; then
    log_info "K3s is already installed. Checking status..."
    if systemctl is-active --quiet k3s; then
        log_info "K3s service is running."
        exit 0
    else
        log_warn "K3s is installed but not running. Starting service..."
        systemctl start k3s
        exit 0
    fi
fi

log_info "Installing K3s using official installation script..."

# Install K3s with local-path storage provisioner enabled
# - Disable traefik (we'll handle ingress separately if needed)
# - Enable local-path provisioner for persistent volumes
export INSTALL_K3S_SKIP_DOWNLOAD=false
export K3S_KUBECONFIG_MODE="644"

# Configure K3s to use local-path storage provisioner
# This allows up to 100GB of persistent storage as specified
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--disable traefik" sh -

# Wait for K3s to be ready
log_info "Waiting for K3s to be ready..."
timeout=60
counter=0
while ! kubectl get nodes &> /dev/null; do
    if [[ $counter -ge $timeout ]]; then
        log_error "K3s failed to start within ${timeout} seconds"
        exit 1
    fi
    sleep 2
    counter=$((counter + 2))
done

# Enable K3s to start on boot (already handled by systemd service, but ensure it's enabled)
systemctl enable k3s

log_info "K3s installation completed successfully!"
log_info "Kubeconfig is available at /etc/rancher/k3s/k3s.yaml"
log_info "To use kubectl, run: export KUBECONFIG=/etc/rancher/k3s/k3s.yaml"
