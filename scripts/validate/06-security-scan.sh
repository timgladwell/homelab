#!/bin/bash
# Scan for HIGH/CRITICAL security issues with Trivy.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
# .claude/ is Claude Code tooling (agent/skill defs, nested git worktrees),
# not cluster manifests — a worktree's content is scanned from within its
# own checkout, not by this scan reaching in from the main tree.
trivy config ./ --severity HIGH,CRITICAL --exit-code 1 --ignorefile .trivyignore.yaml --skip-dirs .claude
