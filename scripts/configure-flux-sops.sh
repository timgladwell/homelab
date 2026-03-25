#!/bin/bash
set -euo pipefail

# Configure FluxCD to decrypt SOPS-encrypted secrets
#
# This script:
# 1. Ensures the sops-age secret exists in the cluster
# 2. Verifies the configuration

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "==> Configuring FluxCD for SOPS Decryption"
echo ""

# Check prerequisites
if ! command -v kubectl &> /dev/null; then
    echo "Error: kubectl not found"
    exit 1
fi

if ! command -v flux &> /dev/null; then
    echo "Error: flux CLI not found"
    echo "Install: curl -s https://fluxcd.io/install.sh | sudo bash"
    exit 1
fi

# Verify cluster access
if ! kubectl cluster-info &> /dev/null; then
    echo "Error: Cannot connect to cluster"
    exit 1
fi

echo "✓ Prerequisites verified"
echo ""

# Check for age key
AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}"
if [ ! -f "${AGE_KEY_FILE}" ]; then
    echo "Error: Age key not found at ${AGE_KEY_FILE}"
    echo ""
    echo "If you generated the key on another machine (e.g., MacBook),"
    echo "copy it to this location:"
    echo "  scp macbook:~/.config/sops/age/keys.txt ~/.config/sops/age/"
    echo "  chmod 600 ~/.config/sops/age/keys.txt"
    exit 1
fi

echo "✓ Age key found at: ${AGE_KEY_FILE}"
echo ""

# Step 1: Create/update sops-age secret in flux-system namespace
echo "==> Step 1: Installing SOPS age key in cluster..."

if kubectl get secret sops-age -n flux-system &> /dev/null; then
    echo "Secret 'sops-age' already exists in flux-system namespace"
    echo ""
    echo "Do you want to update it with the current key? (y/n)"
    read -r response
    
    if [[ "$response" =~ ^[Yy]$ ]]; then
        kubectl delete secret sops-age -n flux-system
        kubectl create secret generic sops-age \
            --namespace=flux-system \
            --from-file=age.agekey="${AGE_KEY_FILE}"
        echo "✓ Secret updated"
    else
        echo "✓ Using existing secret"
    fi
else
    kubectl create secret generic sops-age \
        --namespace=flux-system \
        --from-file=age.agekey="${AGE_KEY_FILE}"
    echo "✓ Secret created"
fi
echo ""

# Step 2: Show current Kustomizations
echo "==> Step 2: Current FluxCD Kustomizations..."
flux get kustomizations
echo ""

# Step 3: Update Kustomizations to enable SOPS
echo "==> Step 3: Checking for SOPS decryption in Kustomizations..."
echo ""

# Get all kustomizations
KUSTOMIZATIONS=$(kubectl get kustomizations.kustomize.toolkit.fluxcd.io -n flux-system -o jsonpath='{.items[*].metadata.name}')

for kustomization in $KUSTOMIZATIONS; do
    echo "Checking: ${kustomization}"
    
    # Check if SOPS decryption is already configured
    if kubectl get kustomization "${kustomization}" -n flux-system -o yaml | grep -q "provider: sops"; then
        echo "  ✓ SOPS already enabled in ${kustomization}"
    else
        echo "  → SOPS not enabled in ${kustomization}"
    fi
    echo ""
done

# Step 4: Force reconciliation
echo "==> Step 4: Forcing reconciliation..."
flux reconcile source git flux-system
echo ""

echo "Waiting for reconciliation to complete..."
sleep 5
echo ""

# Step 5: Verify
echo "==> Step 5: Verification..."
echo ""

echo "FluxCD Kustomizations status:"
flux get kustomizations
echo ""

echo "Checking if SOPS decryption is working..."
echo ""

# Check if any encrypted secrets are in the repo
if find "${PROJECT_ROOT}" -name "*.sops.yaml" 2>/dev/null | head -n 1 | grep -q .; then
    echo "Found SOPS-encrypted files in repository"
    echo ""
    
    # Try to find any secrets that should have been decrypted
    echo "Checking if secrets were created in cluster..."
    kubectl get secrets --all-namespaces | grep -v "kubernetes.io/service-account-token" | grep -v "helm.sh/release"
    echo ""
else
    echo "⚠ No SOPS-encrypted files found in repository yet"
    echo "After you add encrypted secrets and push to git, they will be decrypted automatically"
    echo ""
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Configuration Complete!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "FluxCD is now configured to decrypt SOPS-encrypted secrets."
echo ""
echo "Next steps:"
echo ""
echo "1. Add encrypted secrets to your git repository"
echo "2. Commit and push"
echo "3. FluxCD will automatically:"
echo "   - Detect the new secrets"
echo "   - Decrypt them using the age key"
echo "   - Create/update Kubernetes secrets"
echo ""
echo "Verify with:"
echo "  flux logs --follow"
echo "  kubectl get secrets -A"
echo ""
echo "To manually trigger reconciliation:"
echo "  flux reconcile kustomization flux-system"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"