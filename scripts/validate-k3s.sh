#!/bin/bash
# validate-k3s.sh

set -e

echo "🔍 Validating K3s manifests..."

# 1. YAML syntax
echo "📝 Checking YAML syntax..."
yamllint -f colored ./

# 2. Build kustomizations
echo "🔨 Building kustomizations..."
kustomize build ./clusters/homelab/flux-system > /tmp/k3s-built.yaml

# 3. Schema validation
echo "✅ Validating Kubernetes schemas..."
kubeconform -summary /tmp/k3s-built.yaml

# 4. Best practices
echo "⭐ Checking best practices..."
kube-score score /tmp/k3s-built.yaml

# 5. Security scan
echo "🔒 Security scanning..."
trivy config ./ --severity HIGH,CRITICAL

echo "✨ All validations passed!"