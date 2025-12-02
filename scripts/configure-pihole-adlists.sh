#!/bin/bash
# PiHole Adlist Configuration Script
# Post-deployment script to configure PiHole allow/block lists via API
# This script should be executed by Flux CD after PiHole is deployed and ready
# 
# Usage: ./configure-pihole-adlists.sh <PIHOLE_IP_ADDRESS>
#
# Prerequisites:
# - PiHole must be running and accessible
# - pihole_allow_lists_request_body.json and pihole_block_lists_request_body.json must exist in the repo root

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

# Check for required arguments
if [ $# -lt 1 ]; then
    log_error "Usage: $0 <PIHOLE_IP_ADDRESS>"
    log_error "Example: $0 192.168.1.100"
    exit 1
fi

PIHOLE_IP="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ALLOW_LIST_FILE="${REPO_ROOT}/pihole_allow_lists_request_body.json"
BLOCK_LIST_FILE="${REPO_ROOT}/pihole_block_lists_request_body.json"

# Validate JSON files exist
if [ ! -f "${ALLOW_LIST_FILE}" ]; then
    log_error "Allow list file not found: ${ALLOW_LIST_FILE}"
    exit 1
fi

if [ ! -f "${BLOCK_LIST_FILE}" ]; then
    log_error "Block list file not found: ${BLOCK_LIST_FILE}"
    exit 1
fi

# Wait for PiHole API to be ready
log_info "Waiting for PiHole API to be ready at ${PIHOLE_IP}..."
max_attempts=30
attempt=0

while [ $attempt -lt $max_attempts ]; do
    if curl -s -f "http://${PIHOLE_IP}/admin/versions.php" > /dev/null 2>&1; then
        log_info "PiHole API is ready!"
        break
    fi
    attempt=$((attempt + 1))
    if [ $attempt -eq $max_attempts ]; then
        log_error "PiHole API did not become ready within $max_attempts attempts"
        exit 1
    fi
    sleep 2
done

# Step 1: Update allow list subscriptions
log_info "Step 1: Updating allow list subscriptions..."
if curl -v -i -H "Content-Type: application/json" \
    -X POST \
    -d @"${ALLOW_LIST_FILE}" \
    "http://${PIHOLE_IP}/api/lists?type=allow"; then
    log_info "Allow list subscriptions updated successfully"
else
    log_error "Failed to update allow list subscriptions"
    exit 1
fi

# Step 2: Update block list subscriptions
log_info "Step 2: Updating block list subscriptions..."
if curl -v -i -H "Content-Type: application/json" \
    -X POST \
    -d @"${BLOCK_LIST_FILE}" \
    "http://${PIHOLE_IP}/api/lists?type=block"; then
    log_info "Block list subscriptions updated successfully"
else
    log_error "Failed to update block list subscriptions"
    exit 1
fi

# Step 3: Trigger gravity update (process the adlists)
log_info "Step 3: Triggering gravity update to process adlists..."
if curl -v -i -H "Content-Type: application/json" \
    -X POST \
    "http://${PIHOLE_IP}/api/action/gravity?color=true"; then
    log_info "Gravity update triggered successfully"
else
    log_error "Failed to trigger gravity update"
    exit 1
fi

log_info "PiHole adlist configuration completed successfully!"


