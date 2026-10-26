#!/bin/bash
# Every directory holding a pinned container image must be covered by a
# Dependabot docker entry, or that image silently stops receiving update PRs.
# Every image tag pinned in a HelmRelease's values must carry a `# renovate:`
# comment Renovate can match, or a `# renovate-skip: <reason>`.
#
# This is not hypothetical: the multi-site restructure moved directories out
# from under .github/dependabot.yml's paths, and Dependabot quietly stopped
# proposing bumps for pihole, unbound, python and system-upgrade-controller.
# Nothing failed, so nothing was noticed.
#
# clusters/*/flux-system is excluded on purpose — Flux's own controller images
# are upgraded through the Flux CLI, not by bumping tags in git.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

CONFIG=".github/dependabot.yml"

# Directories listed in dependabot.yml, normalised to repo-relative paths.
listed=$(grep -oE '^[[:space:]]+- "/[^"]*"' "$CONFIG" \
    | sed -E 's/^[[:space:]]*-[[:space:]]*"//; s/"$//; s|^/||' | sort -u)

# Directories containing an image: with an explicit tag. Untagged images
# (e.g. plans.yaml's rancher/k3s-upgrade) carry no version for Dependabot to
# bump, so they are correctly out of scope.
IMAGE_RE='^[[:space:]]*(-[[:space:]]+)?image:[[:space:]]*"?[^ "]+:[^ "]+'
found=$(grep -rlE "$IMAGE_RE" base/ sites/ --include='*.yaml' 2>/dev/null \
    | xargs -n1 dirname 2>/dev/null | sort -u)

fail=0
checked=0
while read -r dir; do
    [[ -n "$dir" ]] || continue
    checked=$((checked + 1))
    if ! grep -qxF "$dir" <<< "$listed"; then
        echo "✗ $dir has a pinned image but is not in $CONFIG"
        grep -nE "$IMAGE_RE" "$dir"/*.yaml | sed 's/^/    /'
        fail=1
    else
        echo "✓ $dir"
    fi
done <<< "$found"

# A listed directory that no longer exists means the config has gone stale the
# other way, which is how the last breakage started.
while read -r dir; do
    [[ -n "$dir" ]] || continue
    # Glob entries (the github-actions "/**") aren't real paths.
    [[ "$dir" == *'*'* ]] && continue
    if [[ ! -d "$dir" ]]; then
        echo "✗ $CONFIG lists $dir, which does not exist"
        fail=1
    fi
done <<< "$listed"

# Renovate's other half. Its customManagers are regexes, so a `# renovate:`
# comment the regex fails to match is invisible — Renovate reports no error, it
# simply never proposes a bump and the pin freezes. Same silent-staleness bug as
# the Dependabot directories above, which is why it lives in the same step.
python3 - <<'PY' || fail=1
import json, pathlib, re, sys

cfg = json.loads(pathlib.Path("renovate.json").read_text())

# A manager only applies to files its managerFilePatterns select, so both are
# checked together: a correct matchString against a file the manager never
# looks at is just as invisible as a regex that fails to match.
managers = []
for m in cfg.get("customManagers", []):
    files = [re.compile(p[1:-1]) for p in m.get("managerFilePatterns", [])
             if p.startswith("/") and p.endswith("/")]
    # Renovate matchStrings use JS named groups; Python spells them (?P<name>).
    strings = [re.compile(re.sub(r"\(\?<(?![=!])", "(?P<", s))
               for s in m.get("matchStrings", [])]
    managers.append((files, strings))

failed = 0
for f in sorted(pathlib.Path(".").rglob("*.y*ml")):
    if ".claude" in f.parts:
        continue
    text = f.read_text(errors="replace")
    declared = text.count("# renovate: datasource")
    if not declared:
        continue
    hits = sum(len(s.findall(text))
               for files, strings in managers
               for s in strings
               if any(fp.search(str(f)) for fp in files))
    if hits == declared:
        print(f"✓ {f} ({hits} renovate pin(s))")
    else:
        print(f"✗ {f}: {declared} '# renovate:' comment(s) but {hits} matched a customManager regex")
        failed = 1

# The other way round: an image tag pinned in a HelmRelease's values with no
# `# renovate:` comment is invisible to every update tool, and step 15 trusts
# a pinned tag as deliberate (scripts/chart_sources.py). So each one needs the
# comment on the line directly above it, or an explicit
# `# renovate-skip: <reason>` there instead, which is printed on every run so
# a skip stays visible rather than becoming the default.
for f in sorted(p for d in ("base", "sites") for p in pathlib.Path(d).rglob("*.y*ml")):
    lines = f.read_text(errors="replace").splitlines()
    if not any(re.match(r"kind: HelmRelease\s*$", l) for l in lines):
        continue
    for i, line in enumerate(lines):
        if not re.match(r"\s+tag:\s*\S", line):
            continue
        above = next((lines[j].strip() for j in range(i - 1, -1, -1) if lines[j].strip()), "")
        skip = re.match(r"# renovate-skip:\s*(.*)", above)
        if above.startswith("# renovate: datasource"):
            continue  # counted and matched above
        if skip and skip.group(1).strip():
            print(f"~ {f}:{i + 1}: pinned without Renovate: {skip.group(1).strip()}")
        else:
            print(f"✗ {f}:{i + 1}: '{line.strip()}' has no '# renovate:' comment above it"
                  + (" (renovate-skip needs a reason)" if skip else "")
                  + "; add one, or '# renovate-skip: <why>'")
            failed = 1
sys.exit(failed)
PY

renovate_count=$(grep -rlE '# renovate(: datasource|-skip:)' --include='*.yaml' --include='*.yml' \
    . 2>/dev/null | grep -v '/\.claude/' | xargs grep -chE '# renovate(: datasource|-skip:)' \
    | awk '{s += $1} END {print s + 0}')

# Zero means a matcher stopped matching — which would otherwise read as
# "every dependency is covered".
echo "CHECKED $((checked + renovate_count)) pinned dependencies"
exit $fail
