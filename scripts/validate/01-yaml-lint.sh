#!/bin/bash
# Validate YAML syntax across the repository.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
yamllint -f colored ./
