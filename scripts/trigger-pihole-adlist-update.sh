#!/bin/bash
# Trigger PiHole Adlist Update Job
# This script manages the PiHole adlist configuration Job
# Run this after updating the adlist configuration in k8s/configmap-adlists.yaml
#
# Usage: ./trigger-pihole-adlist-update.sh [--apply]
#   --apply: Apply the Job directly for immediate execution (otherwise just delete for Flux CD to recreate)

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

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    log_error "kubectl is not available. Please ensure K3s is installed and kubectl is configured."
    exit 1
fi

# Check if namespace exists
if ! kubectl get namespace pihole &> /dev/null; then
    log_error "Namespace 'pihole' does not exist. Please deploy PiHole first."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Check for --apply flag
APPLY_DIRECTLY=false
if [[ "${1:-}" == "--apply" ]]; then
    APPLY_DIRECTLY=true
fi

if [ "$APPLY_DIRECTLY" = true ]; then
    log_info "Applying ConfigMap and Job directly for immediate execution..."
    
    # Apply ConfigMap first
    if kubectl apply -f "${REPO_ROOT}/k8s/configmap-adlists.yaml"; then
        log_info "ConfigMap applied successfully"
    else
        log_error "Failed to apply ConfigMap"
        exit 1
    fi
    
    # Delete existing Job if it exists
    kubectl delete job -n pihole pihole-adlist-config --ignore-not-found=true
    
    # Apply the Job
    if kubectl apply -f "${REPO_ROOT}/k8s/pihole-post-deploy-job.yaml"; then
        log_info "Job applied successfully and will run immediately"
    else
        log_error "Failed to apply Job"
        exit 1
    fi
else
    log_info "Deleting existing PiHole adlist configuration Job..."
    
    # Delete the Job - Flux CD will recreate it on next sync
    if kubectl delete job -n pihole pihole-adlist-config --ignore-not-found=true; then
        log_info "Job deleted successfully. Flux CD will recreate it on next sync."
    else
        log_warn "Job may not have existed. It will be created when Flux CD syncs."
    fi
    
    log_info ""
    log_info "Next steps:"
    log_info "1. Ensure configmap-adlists.yaml is updated with the latest JSON file contents"
    log_info "2. Commit and push changes to the GitOps repository"
    log_info "3. Flux CD will automatically sync and recreate the Job"
    log_info ""
    log_info "To trigger the Job immediately (without waiting for Flux CD sync), run:"
    log_info "  $0 --apply"
fi

log_info ""
log_info "Monitor the Job status with:"
log_info "  kubectl get jobs -n pihole"
log_info "  kubectl logs -n pihole -l app=pihole-adlist-config --tail=50 -f"
