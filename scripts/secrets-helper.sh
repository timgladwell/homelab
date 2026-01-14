#!/bin/bash
set -euo pipefail

# Helper script for secrets management operations
# This script provides common secrets management workflows

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SECRETS_DIR="${PROJECT_ROOT}/secrets"

usage() {
    cat <<USAGE
Secrets Management Helper

Usage: $0 <command> <file>

Commands:
    edit <file>      - Edit encrypted file (decrypts, opens editor, re-encrypts)
    encrypt <file>   - Encrypt a plaintext file
    decrypt <file>   - Decrypt to stdout
    view <file>      - View decrypted contents (doesn't save)
    rotate           - Rotate encryption keys (advanced)

Examples:
    $0 edit secrets/env/homelab.env
    $0 encrypt secrets/env/production.env
    $0 view secrets/env/homelab.env

Environment Variables:
    SOPS_AGE_KEY_FILE - Path to age private key (required)
                        Default: ~/.config/sops/age/keys.txt

Note: Always use 'edit' for modifying secrets - it handles encryption automatically
USAGE
    exit 1
}

check_sops() {
    if ! command -v sops &> /dev/null; then
        echo "Error: SOPS not installed. Run bootstrap-secrets.sh first."
        exit 1
    fi
    
    # Check for age key
    if [ -z "${SOPS_AGE_KEY_FILE:-}" ]; then
        echo "Warning: SOPS_AGE_KEY_FILE not set"
        echo "Using default: ~/.config/sops/age/keys.txt"
        echo ""
        echo "To avoid this warning, add to your shell profile:"
        echo "  export SOPS_AGE_KEY_FILE=\"\$HOME/.config/sops/age/keys.txt\""
        export SOPS_AGE_KEY_FILE="${HOME}/.config/sops/age/keys.txt"
    fi
    
    if [ ! -f "${SOPS_AGE_KEY_FILE}" ]; then
        echo "Error: Age key not found at: ${SOPS_AGE_KEY_FILE}"
        echo "Run bootstrap-secrets.sh to generate a key"
        exit 1
    fi
}

[[ $# -lt 1 ]] && usage

COMMAND=$1
FILE=${2:-}

check_sops

case "${COMMAND}" in
    edit)
        [[ -z "${FILE}" ]] && usage
        sops "${FILE}"
        ;;
    encrypt)
        [[ -z "${FILE}" ]] && usage
        if [[ -f "${FILE}" ]]; then
            sops -e "${FILE}" > "${FILE}.tmp"
            mv "${FILE}.tmp" "${FILE}"
            echo "✓ File encrypted: ${FILE}"
        else
            echo "Error: File not found: ${FILE}"
            exit 1
        fi
        ;;
    decrypt)
        [[ -z "${FILE}" ]] && usage
        sops -d "${FILE}"
        ;;
    view)
        [[ -z "${FILE}" ]] && usage
        sops -d "${FILE}" | less
        ;;
    rotate)
        echo "Key rotation is an advanced operation."
        echo "See: https://github.com/getsops/sops#rotating-keys"
        echo ""
        echo "Basic process:"
        echo "1. Add new age public key to .sops.yaml"
        echo "2. Run: sops updatekeys <file>"
        echo "3. Remove old key from .sops.yaml"
        ;;
    *)
        echo "Error: Unknown command: ${COMMAND}"
        usage
        ;;
esac
