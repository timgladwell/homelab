#!/bin/bash
# Create PiHole Secret Script
# Generates a Kubernetes secret for PiHole configuration from environment variables
# 
# Usage: 
#   export LOCAL_DOMAIN="example.local"
#   export GATEWAY_IP="192.168.1.1"
#   export SUBNET_MASK="255.255.0.0"
#   ./create-pihole-secret.sh

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Configuration - should be set via environment variables
LOCAL_DOMAIN="${LOCAL_DOMAIN:-example.local}" # TODO - flip this over to the navigable domain
GATEWAY_IP="${GATEWAY_IP:-${`ip route show | grep default | awk '{print $3}'`}}"
SUBNET_MASK="${SUBNET_MASK:-255.255.0.0}"

# Validate that LOCAL_DOMAIN is set and non-empty
if [[ ! "$LOCAL_DOMAIN" =~ [^[:space:]] ]]; then
    log_error "LOCAL_DOMAIN is empty or contains only whitespace."
    log_error "Please set LOCAL_DOMAIN environment variable or ensure a default route exists."
    exit 1
fi

# Validate that GATEWAY_IP is set and non-empty
if [[ ! "$GATEWAY_IP" =~ [^[:space:]] ]]; then
    log_error "GATEWAY_IP is empty or contains only whitespace."
    log_error "Please set GATEWAY_IP environment variable or ensure a default route exists."
    exit 1
fi

# Validate that SUBNET_MASK is set and non-empty
if [[ ! "$SUBNET_MASK" =~ [^[:space:]] ]]; then
    log_error "SUBNET_MASK is empty or contains only whitespace."
    log_error "Please set SUBNET_MASK environment variable or ensure a default route exists."
    exit 1
fi

# Calculate CIDR notation from subnet mask and gateway IP
# Format: <enabled>,<cidr-ip-address-range>,<server-ip-address>,<domain>
IFS='.' read -r -a MASK_ARRAY <<< "${SUBNET_MASK}"
IFS='.' read -r -a GATEWAY_ARRAY <<< "${GATEWAY_IP}"

# Determine CIDR prefix length from subnet mask
# For /16 subnet (255.255.0.0), CIDR is first two octets with /16
# For /24 subnet (255.255.255.0), CIDR is first three octets with /24
if [ "${MASK_ARRAY[0]}" = "255" ] && [ "${MASK_ARRAY[1]}" = "255" ] && [ "${MASK_ARRAY[2]}" = "255" ]; then
    # /24 subnet
    CIDR_RANGE="${GATEWAY_ARRAY[0]}.${GATEWAY_ARRAY[1]}.${GATEWAY_ARRAY[2]}.0/24"
elif [ "${MASK_ARRAY[0]}" = "255" ] && [ "${MASK_ARRAY[1]}" = "255" ]; then
    # /16 subnet
    CIDR_RANGE="${GATEWAY_ARRAY[0]}.${GATEWAY_ARRAY[1]}.0.0/16"
else
    log_error "This script currently only supports /16 (255.255.0.0) or /24 (255.255.255.0) subnets"
    log_error "Please manually set REVERSE_SERVER_CONFIG in the secret with the correct CIDR format"
    exit 1
fi

# Generate conditional forwarding CSV format: enabled,cidr-range,server-ip,domain
REVERSE_SERVER_CONFIG="true,${CIDR_RANGE},${GATEWAY_IP},${LOCAL_DOMAIN}"

log_info "Creating PiHole secret with the following configuration:"
log_info "  Local Domain: ${LOCAL_DOMAIN}"
log_info "  Gateway IP: ${GATEWAY_IP}"
log_info "  Subnet Mask: ${SUBNET_MASK}"
log_info "  CIDR Range: ${CIDR_RANGE}"
log_info "  Conditional Forwarding: ${REVERSE_SERVER_CONFIG}"

# Check if namespace exists
if ! kubectl get namespace pihole &> /dev/null; then
    log_error "Namespace 'pihole' does not exist. Please create it first."
    exit 1
fi

# Delete existing secret if it exists
if kubectl get secret pihole-secret -n pihole &> /dev/null; then
    log_info "Deleting existing secret..."
    kubectl delete secret pihole-secret -n pihole
fi

# Create the secret
kubectl create secret generic pihole-secret \
    --namespace=pihole \
    --from-literal=FTLCONF_dns_domain_name="${LOCAL_DOMAIN}" \
    --from-literal=FTLCONF_dns_revServers="${REVERSE_SERVER_CONFIG}"

log_info "PiHole secret created successfully!"

