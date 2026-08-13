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
| Variable References | PASS |
| Policy (conftest) | PASS |
| CRD Availability | PASS |
| Security Scan | FAIL |
| Dependency Coverage | PASS |
| Secrets Encrypted | PASS |
```

That is the order `validate-k3s.sh` prints — the three build-gated steps
(Variable References, Policy, CRD Availability) run *before* Security Scan,
Dependency Coverage and Secrets Encrypted, not after.

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
- Never paste output from PASS or SKIP steps. Nothing in a passing step needs
  reporting — tool-version drift is handled by the Renovate pins in
  `.github/workflows/validate.yml`, not by anything this pipeline prints.
- Ignore the `CHECKED <n> <noun>` line each step prints; it feeds the harness's
  coverage invariant, not the report. The exception is a step that failed
  *because* of it — `checked 0 items` or `printed no CHECKED line`. That is not
  a manifest problem, it means the step validated nothing (usually site or
  layer discovery came back empty), so report it as its own failure and say so
  plainly rather than looking for a manifest to blame.
- Every severity is a failure, not just HIGH/CRITICAL or "warning" grades —
  the underlying scripts already enforce this (trivy runs unfiltered by
  severity, kube-score treats any non-OK grade as a finding, conftest runs
  with `--fail-on-warn`). Don't second-guess a FAIL by re-triaging severity;
  report it as failed and let the calling conversation decide whether to add
  an ignore-rule (`.trivyignore.yaml`, a kube-score `--ignore-test`, or a
  conftest policy exception).

End with the script's own final summary line (`Passed: N  Failed: N  Skipped: N`).
