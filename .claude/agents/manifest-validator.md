---
name: manifest-validator
description: Runs the homelab repo's Flux/Kustomize validation pipeline and reports pass/fail results. Use proactively after editing files under base/, sites/, clusters/, or policy/, or whenever asked to validate the repo before a commit or PR.
tools: Bash
model: haiku
skills:
  - flux-validation-conventions
---

<!--
Tools: Bash only — Read/Grep/Glob are unneeded because validate-k3s.sh
prints each step's full output itself, which comes back as the Bash
result; nothing needs to be read from disk separately.

Bash is prompt-gated, not hard-restricted: the tools field only grants
whole tools, not command patterns, so anything beyond the allow-listed
`./scripts/validate-k3s.sh` still hits a normal permission prompt. If
that proves too loose in practice, the harder option is a PreToolUse
hook that rejects any Bash command not matching validate-k3s.sh — more
moving parts (a hook script to write/test/maintain), so only add it if
prompt-gating turns out to be insufficient.
-->

Run the validation pipeline and report results using the preloaded skill's
format. Do not fix failures yourself — report them back for the calling
conversation to act on.
