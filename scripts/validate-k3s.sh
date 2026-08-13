#!/bin/bash
# validate-k3s.sh — runs all validation steps and reports a structured summary.
# Each step runs independently; failures do not prevent subsequent steps from running.
# The output is structured so that failures are easy to identify and fix.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VALIDATE_DIR="$SCRIPT_DIR/validate"
export K3S_BUILD_DIR="${TMPDIR:-/tmp}"

PASS=0
FAIL=0
SKIP=0
declare -a RESULTS

run_step() {
    local name="$1"
    local script="$2"

    echo ""
    echo "================================================================"
    echo "=== VALIDATION: $name ==="
    echo "================================================================"

    local output
    output=$(bash "$script" 2>&1)
    local exit_code=$?

    echo "$output"

    # Coverage invariant. Every step ends with "CHECKED <n> <noun>" saying how
    # many things it actually looked at, and a step that looked at nothing is a
    # failure regardless of its exit code.
    #
    # This exists because the pipeline discovers its own work: sites come from a
    # sites/*/ glob, layers from the Flux Kustomization objects. Discovery
    # returning empty makes every per-site loop iterate zero times and exit 0 —
    # five steps report PASS having validated nothing. Same shape of bug as a
    # scan whose file matcher stops matching, or an over-broad --skip-dirs.
    # One check here covers all of them, and covers steps added later for free.
    local checked
    checked=$(grep -oE '^CHECKED [0-9]+' <<< "$output" | tail -1 | awk '{print $2}')
    if [[ -z "$checked" ]]; then
        echo "[FAIL] $name — step printed no CHECKED line (see the coverage invariant in validate-k3s.sh)"
        RESULTS+=("FAIL  $name (no CHECKED line)")
        ((FAIL++))
        return 1
    fi
    if [[ "$checked" -eq 0 ]]; then
        echo "[FAIL] $name — checked 0 items; nothing was validated"
        RESULTS+=("FAIL  $name (checked nothing)")
        ((FAIL++))
        return 1
    fi

    if [[ $exit_code -eq 0 ]]; then
        echo "[PASS] $name"
        RESULTS+=("PASS  $name")
        ((PASS++))
        return 0
    else
        echo "[FAIL] $name (exit code: $exit_code)"
        RESULTS+=("FAIL  $name")
        ((FAIL++))
        return 1
    fi
}

skip_step() {
    local name="$1"
    local reason="$2"
    echo ""
    echo "================================================================"
    echo "=== VALIDATION: $name ==="
    echo "================================================================"
    echo "[SKIP] $name — $reason"
    RESULTS+=("SKIP  $name ($reason)")
    ((SKIP++))
}

# Step 1: YAML lint — independent
run_step "YAML Lint" "$VALIDATE_DIR/01-yaml-lint.sh"

# Step 2: Flux build — gates kustomize build and all downstream steps
if run_step "Flux Build" "$VALIDATE_DIR/02-flux-build.sh"; then
    flux_build_ok=true
else
    flux_build_ok=false
fi

if [[ "$flux_build_ok" == true ]]; then
    # Step 3: Kustomize build — gates schema, best practices, variable refs, conftest
    if run_step "Kustomize Build" "$VALIDATE_DIR/03-kustomize-build.sh"; then
        build_ok=true
    else
        build_ok=false
    fi

    if [[ "$build_ok" == true ]]; then
        run_step "Schema Validation" "$VALIDATE_DIR/04-schema-validate.sh"
        run_step "Best Practices" "$VALIDATE_DIR/05-best-practices.sh"
        run_step "Variable References" "$VALIDATE_DIR/07-variable-check.sh"
        run_step "Policy (conftest)" "$VALIDATE_DIR/08-conftest.sh"
        run_step "CRD Availability" "$VALIDATE_DIR/11-crd-availability.sh"
    else
        skip_step "Schema Validation" "kustomize build failed"
        skip_step "Best Practices" "kustomize build failed"
        skip_step "Variable References" "kustomize build failed"
        skip_step "Policy (conftest)" "kustomize build failed"
        skip_step "CRD Availability" "kustomize build failed"
    fi
else
    skip_step "Kustomize Build" "flux build failed"
    skip_step "Schema Validation" "flux build failed"
    skip_step "Best Practices" "flux build failed"
    skip_step "Variable References" "flux build failed"
    skip_step "Policy (conftest)" "flux build failed"
    skip_step "CRD Availability" "flux build failed"
fi

# Step 6: Security scan — independent
run_step "Security Scan" "$VALIDATE_DIR/06-security-scan.sh"

# Step 9: Dependency coverage — independent (reads the repo, not the build output)
run_step "Dependency Coverage" "$VALIDATE_DIR/09-dependabot-coverage.sh"

# Step 10: Secrets encrypted — independent
run_step "Secrets Encrypted" "$VALIDATE_DIR/10-secrets-encrypted.sh"

# Summary
echo ""
echo "================================================================"
echo "=== VALIDATION SUMMARY ==="
echo "================================================================"
for r in "${RESULTS[@]}"; do
    echo "  $r"
done
echo ""
echo "  Passed: $PASS  Failed: $FAIL  Skipped: $SKIP"
echo "================================================================"

if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
