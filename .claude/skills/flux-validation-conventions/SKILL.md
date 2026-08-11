---
name: flux-validation-conventions
description: How to run this repo's validation pipeline and report results. Use whenever validating Flux/Kustomize manifests, SOPS secrets, or checking a change is ready to commit.
---

# Flux validation conventions

Run the full pipeline from the repo root:

```bash
./scripts/validate-k3s.sh
```

Do not run individual `validate/NN-*.sh` scripts standalone unless asked — the
top-level script encodes the correct gating order (steps 2 and 3 gate later
steps; see CLAUDE.md's Validation section for the full step list).

## Reporting format

Report results as a pass/fail table, one row per step, in the order the
script printed them:

```
| Step | Result |
|---|---|
| YAML Lint | PASS |
| Flux Build | PASS |
| Kustomize Build | PASS |
| Schema Validation | PASS |
| Best Practices | PASS |
| Security Scan | FAIL |
| Dependabot Coverage | PASS |
| Secrets Encrypted | PASS |
| Variable References | SKIP (kustomize build failed) |
| Policy (conftest) | SKIP (kustomize build failed) |
| CRD Availability | SKIP (kustomize build failed) |
```

Below the table:

- If everything passed: one line, "All checks passed." Nothing else — do not
  paste passing step output.
- For each FAILED step: the step name as a heading, then only that step's
  error output (the relevant lines — no need to paste successful sub-checks
  within a failing step), and a one-line diagnosis of the root cause if it's
  apparent from the error text alone. Do not run extra commands (`git log`,
  `git worktree list`, re-reading manifests, etc.) to investigate further —
  this pipeline must stay a fast, stateless "run script, report result"
  operation. If the cause isn't obvious from the output text, say so and
  report the raw error instead of guessing.
- Never paste output from PASS or SKIP steps.

End with the script's own final summary line (`Passed: N  Failed: N  Skipped: N`).
