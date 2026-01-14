#!/bin/bash
set -euo pipefail

# Install git hooks to prevent committing unencrypted secrets
# Run this once in your repository to set up safety checks

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
GIT_HOOKS_DIR="${PROJECT_ROOT}/.git/hooks"

echo "==> Installing Git Hooks for Secrets Safety"
echo ""

# Ensure we're in a git repository
if [ ! -d "${PROJECT_ROOT}/.git" ]; then
    echo "Error: Not in a git repository"
    exit 1
fi

# Create hooks directory if it doesn't exist
mkdir -p "${GIT_HOOKS_DIR}"

# ═══════════════════════════════════════════════════════════════
# Hook 1: pre-commit - Prevent committing unencrypted secrets
# ═══════════════════════════════════════════════════════════════

cat > "${GIT_HOOKS_DIR}/pre-commit" <<'HOOK_EOF'
#!/bin/bash
# Git pre-commit hook to prevent committing unencrypted secrets

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

echo "🔒 Checking for unencrypted secrets..."

# Get list of files being committed
FILES=$(git diff --cached --name-only --diff-filter=ACM)

# Track if we found any issues
FOUND_ISSUES=0

# Check each file
for FILE in $FILES; do
    # Only check YAML files that should contain secrets
    if [[ "$FILE" =~ secret.*\.yaml$ ]] || [[ "$FILE" =~ .*secret\.yaml$ ]]; then
        
        # Check if file exists (might be deleted)
        if [ ! -f "$FILE" ]; then
            continue
        fi
        
        # Check if file has 'sops:' metadata section
        if ! grep -q "^sops:" "$FILE"; then
            echo -e "${RED}✗ UNENCRYPTED SECRET DETECTED: $FILE${NC}"
            echo "  This file appears to be a secret but is not encrypted with SOPS!"
            FOUND_ISSUES=1
            continue
        fi
        
        # Check if file has ENC[] markers (indicates encrypted values)
        if ! grep -q "ENC\[" "$FILE"; then
            echo -e "${YELLOW}⚠ WARNING: $FILE${NC}"
            echo "  File has SOPS metadata but no encrypted values."
            echo "  This might be intentional (empty secret) or an error."
            echo ""
            echo "  If this is a Kubernetes secret, ensure it has data: or stringData: fields"
            echo "  and that your .sops.yaml encrypted_regex matches these fields."
            echo ""
            read -p "Continue anyway? (y/N) " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                FOUND_ISSUES=1
            fi
        else
            echo -e "${GREEN}✓ $FILE is encrypted${NC}"
        fi
    fi
    
    # Additional check: Look for common secret patterns in non-secret files
    if [[ ! "$FILE" =~ secret.*\.yaml$ ]] && [[ "$FILE" =~ \.(yaml|yml|env)$ ]]; then
        # Check for common password/key patterns
        if grep -iE "(password|api[_-]?key|secret|token|credential):\s*['\"]?[a-zA-Z0-9]{8,}" "$FILE" 2>/dev/null | \
           grep -vE "(example|sample|test|changeme|your-|my-|ENC\[)" | \
           grep -q .; then
            
            echo -e "${YELLOW}⚠ POSSIBLE SECRET IN: $FILE${NC}"
            echo "  Found potential secret values in a non-secret file:"
            grep -iE "(password|api[_-]?key|secret|token|credential):\s*['\"]?[a-zA-Z0-9]{8,}" "$FILE" | \
                grep -vE "(example|sample|test|changeme|your-|my-|ENC\[)" | head -3
            echo ""
            read -p "Are you sure you want to commit this? (y/N) " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                FOUND_ISSUES=1
            fi
        fi
    fi
done

if [ $FOUND_ISSUES -eq 1 ]; then
    echo ""
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${RED}COMMIT BLOCKED: Unencrypted secrets detected!${NC}"
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "To fix:"
    echo "  1. Encrypt secrets with: ./scripts/secrets-helper.sh edit <file>"
    echo "  2. Verify encryption: grep 'ENC\[' <file>"
    echo "  3. Try committing again"
    echo ""
    echo "To bypass this check (NOT RECOMMENDED):"
    echo "  git commit --no-verify"
    echo ""
    exit 1
fi

echo -e "${GREEN}✓ All secret files are properly encrypted${NC}"
exit 0
HOOK_EOF

chmod +x "${GIT_HOOKS_DIR}/pre-commit"
echo "✓ Installed: pre-commit hook"

# ═══════════════════════════════════════════════════════════════
# Hook 2: pre-push - Double-check before pushing to remote
# ═══════════════════════════════════════════════════════════════

cat > "${GIT_HOOKS_DIR}/pre-push" <<'HOOK_EOF'
#!/bin/bash
# Git pre-push hook - Final safety check before pushing

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

echo "🔒 Final secrets check before push..."

# Find all secret files in the repository
SECRET_FILES=$(find . -type f \( -name "*secret*.yaml" -o -name "*secret.yaml" \) -not -path "./.git/*")

FOUND_ISSUES=0

for FILE in $SECRET_FILES; do
    if [ ! -f "$FILE" ]; then
        continue
    fi
    
    # Skip if encrypted properly
    if grep -q "^sops:" "$FILE" && grep -q "ENC\[" "$FILE"; then
        continue
    fi
    
    # Check if it's a template or example (those are OK to be unencrypted)
    if [[ "$FILE" =~ \.template$ ]] || [[ "$FILE" =~ example ]]; then
        continue
    fi
    
    echo -e "${RED}✗ UNENCRYPTED: $FILE${NC}"
    FOUND_ISSUES=1
done

if [ $FOUND_ISSUES -eq 1 ]; then
    echo ""
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${RED}PUSH BLOCKED: Unencrypted secrets found in repository!${NC}"
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "Encrypt all secrets before pushing."
    echo ""
    exit 1
fi

echo -e "${GREEN}✓ All secrets properly encrypted${NC}"
exit 0
HOOK_EOF

chmod +x "${GIT_HOOKS_DIR}/pre-push"
echo "✓ Installed: pre-push hook"

# ═══════════════════════════════════════════════════════════════
# Hook 3: commit-msg - Warn if commit message mentions secrets
# ═══════════════════════════════════════════════════════════════

cat > "${GIT_HOOKS_DIR}/commit-msg" <<'HOOK_EOF'
#!/bin/bash
# Git commit-msg hook - Check for sensitive info in commit messages

COMMIT_MSG=$(cat "$1")
YELLOW='\033[1;33m'
NC='\033[0m'

# Check if commit message contains words like "password", "secret", etc.
if echo "$COMMIT_MSG" | grep -iE "(password|secret|key|token|credential).*=|:.*[a-zA-Z0-9]{10,}" | grep -q .; then
    echo ""
    echo -e "${YELLOW}⚠ WARNING: Your commit message may contain sensitive information!${NC}"
    echo ""
    echo "Commit message:"
    echo "$COMMIT_MSG"
    echo ""
    read -p "Continue with this commit message? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

exit 0
HOOK_EOF

chmod +x "${GIT_HOOKS_DIR}/commit-msg"
echo "✓ Installed: commit-msg hook"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Git Hooks Installed Successfully! ✓"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Installed hooks:"
echo "  • pre-commit  - Prevents committing unencrypted secrets"
echo "  • pre-push    - Final check before pushing to remote"
echo "  • commit-msg  - Warns if commit message contains secrets"
echo ""
echo "Test the hooks:"
echo "  1. Try to commit an unencrypted secret (should block)"
echo "  2. Encrypt it and commit again (should succeed)"
echo ""
echo "To bypass hooks (emergency only):"
echo "  git commit --no-verify"
echo "  git push --no-verify"
echo ""
echo "Note: Git hooks are local only. Team members should also run:"
echo "  ./scripts/setup-git-hooks.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"