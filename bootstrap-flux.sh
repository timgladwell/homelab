#!/bin/bash
# Flux CD Bootstrap Script
# Installs Flux CD CLI via Homebrew and configures GitOps repository
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

# Configuration - these should be set via environment variables or passed as arguments
GITOPS_REPO_URL="${GITOPS_REPO_URL:-https://github.com/timgladwell/homelab}"
GITOPS_BRANCH="${GITOPS_BRANCH:-main}"
FLUX_NAMESPACE="${FLUX_NAMESPACE:-flux-system}"

log_info "Installing Flux CD CLI via Homebrew..."

# Check if Homebrew is installed
if ! command -v brew &> /dev/null; then
    log_error "Homebrew is not installed. Please install Homebrew first."
    log_info "Install Homebrew: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    exit 1
fi

# Install Flux CD CLI (idempotent)
if command -v flux &> /dev/null; then
    log_info "Flux CD CLI is already installed."
    flux version
else
    log_info "Installing Flux CD CLI..."
    brew install fluxcd/tap/flux
fi

# Check if kubectl is available and K3s is running
if ! command -v kubectl &> /dev/null; then
    log_error "kubectl is not available. Please ensure K3s is installed first."
    exit 1
fi

# Check if K3s cluster is accessible
if ! kubectl cluster-info &> /dev/null; then
    log_error "Unable to connect to K3s cluster. Please ensure K3s is running."
    exit 1
fi

log_info "Installing Flux CD into the cluster..."

# Check if Flux is already installed
if kubectl get namespace "${FLUX_NAMESPACE}" &> /dev/null; then
    log_warn "Flux CD appears to be already installed in namespace ${FLUX_NAMESPACE}"
    log_info "If you need to reconfigure, please uninstall first: flux uninstall"
    
    # Check if GitRepository already exists
    if kubectl get gitrepository -n "${FLUX_NAMESPACE}" &> /dev/null; then
        log_info "Flux CD GitOps is already configured."
        exit 0
    fi
else
    # Install Flux CD
    log_info "Bootstrapping Flux CD into the cluster..."
    flux bootstrap github \
        --owner=timgladwell \
        --repository=homelab \
        --branch="${GITOPS_BRANCH}" \
        --path=./k8s \
        --namespace="${FLUX_NAMESPACE}" \
        --network-policy=false
fi

log_info "Flux CD installation completed successfully!"
