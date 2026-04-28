#!/bin/bash
# Scan for HIGH/CRITICAL security issues with Trivy.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
trivy config ./ --severity HIGH,CRITICAL --exit-code 1 --ignorefile .trivyignore.yaml
