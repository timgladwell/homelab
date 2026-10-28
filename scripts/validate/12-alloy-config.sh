#!/bin/bash
# Validate every Alloy river config this repo ships, per site.
#
# The rest of the pipeline cannot see inside these. A HelmRelease's `values:`
# is opaque YAML to kustomize, flux build and kubeconform alike — the Alloy
# config is a string in a string, and the chart only renders it on the cluster.
# So a misspelled attribute name passes all eleven other steps and CrashLoops
# the collector after merge, at every site at once, taking metrics and logs
# down together. `alloy validate` parses the config the way the process does at
# startup, which catches exactly that class of error.
#
# Configs are discovered from the built output rather than listed here, so a
# second Alloy release (Akron already runs two) is covered without touching
# this file. They come from the built output rather than straight from the
# .alloy files in the repo because that is where they are already hydrated with
# each site's cluster-vars (step 3) — without that every ${VAR} is a parse error.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="${K3S_BUILD_DIR:-${TMPDIR:-/tmp}}"
cd "$REPO_ROOT"
source "$(dirname "$0")/lib-sites.sh"

if ! command -v alloy >/dev/null 2>&1; then
    echo "ERROR: alloy not found on PATH." >&2
    echo "  Install it from https://github.com/grafana/alloy/releases —" >&2
    echo "  note 'brew install alloy' is an unrelated package." >&2
    exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail=0
checked=0
for site in $(sites); do
    BUILD_OUTPUT="${BUILD_DIR}/k3s-built-${site}.yaml"
    if [[ ! -f "$BUILD_OUTPUT" ]]; then
        echo "ERROR: $BUILD_OUTPUT not found — run 03-kustomize-build.sh first" >&2
        exit 1
    fi
    echo "--- $site ---"
    mkdir -p "$WORK/$site"

    python3 - "$BUILD_OUTPUT" "$WORK/$site" <<'PY' || exit 1
import pathlib, re, sys, yaml

built, outdir = sys.argv[1], pathlib.Path(sys.argv[2])
docs = [d for d in yaml.safe_load_all(pathlib.Path(built).read_text()) if d]

# Each Alloy release names the ConfigMap holding its config. Pair the two up so
# the config can be validated with the release's own settings.
#
# Alloy refuses to run a component below the process's stability level, so a
# release that has not opted in must not validate as though it had. Each release
# that needs one sets the chart's alloy.stabilityLevel: the collector runs at
# experimental, for loki.write's WAL alone, and the node log reader at the
# default. The chart always renders
# --stability.level=<alloy.stabilityLevel>, so that value is the only one read
# here. A copy in extraArgs would be a second flag that wins only by coming
# last; step 15's policy (policy/rendered_chart_settings.rego) rejects it.
#
# `alloy validate` enforces a component's own stability level, but not a gate a
# component applies to its arguments when it is built: as of 1.21 it accepts
# loki.write's `wal { enabled = true }` at any level, while `alloy run` refuses
# it below experimental. A gate of that kind is caught only on the cluster.
#
# Experimental is process-wide, so validating at it alone would admit any
# experimental component added later. The WAL is the only thing a release is
# allowed to need it for, so such a release is validated a second time, with
# every wal block stripped, at generally-available. The first pass still checks
# the wal blocks' own attributes. A release that genuinely needs another
# experimental component fails here until this rule is revisited.
WAL = re.compile(r"\n[ \t]*wal[ \t]*\{")

def strip_wal(text):
    # Brace-matched, so a wal block with nested blocks is removed whole. A brace
    # inside a string or comment in the block would miscount, and fail loudly.
    out, i = [], 0
    while m := WAL.search(text, i):
        out.append(text[i:m.start()])
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
    return "".join(out + [text[i:]])

assert strip_wal('a {\n  wal {\n    b { c = 1 }\n  }\n  d = 2\n}\nwal {}') \
    == 'a {\n  d = 2\n}'

releases = {}
for doc in docs:
    if doc.get("kind") != "HelmRelease":
        continue
    alloy = doc.get("spec", {}).get("values", {}).get("alloy", {})
    cm = alloy.get("configMap", {})
    if cm.get("content"):
        sys.exit(f"ERROR: {doc['metadata']['name']} inlines its Alloy config in "
                 "values.alloy.configMap.content. Put it in a .alloy file and "
                 "generate the ConfigMap, so alloy fmt/validate can be pointed "
                 "at it directly.")
    if not cm.get("name"):
        continue
    level = alloy.get("stabilityLevel")
    releases[cm["name"]] = (doc["metadata"]["name"], cm.get("key", "config.alloy"),
                            [f"--stability.level={level}"] if level else [])

for doc in docs:
    if doc.get("kind") != "ConfigMap":
        continue
    entry = releases.pop(doc["metadata"]["name"], None)
    if not entry:
        continue
    name, key, flags = entry
    # key "." is the chart's `run /etc/alloy/.`: Alloy loads every *.alloy key
    # as one configuration, so the assembled directory is what gets validated
    # (ADR 0003: a receiver's file isn't valid on its own).
    files = ({k: v for k, v in doc["data"].items() if k.endswith(".alloy")}
             if key == "." else {"config.alloy": doc["data"][key]})
    if not files:
        sys.exit(f"ERROR: {name} loads ConfigMap {doc['metadata']['name']} as a "
                 "directory, but it has no *.alloy keys.")
    (outdir / f"{name}.args").write_text(" ".join(flags))
    ga = flags and flags[-1] == "--stability.level=experimental"
    for k, v in files.items():
        (outdir / name).mkdir(exist_ok=True)
        (outdir / name / k).write_text(v)
        if ga:
            (outdir / "ga" / name).mkdir(parents=True, exist_ok=True)
            (outdir / "ga" / name / k).write_text(strip_wal(v))

# A release pointing at a ConfigMap nothing generates would otherwise vanish
# from this step silently, and CrashLoop on the cluster.
if releases:
    sys.exit("ERROR: no ConfigMap in the built output for: " + ", ".join(releases))
PY

    for config in "$WORK/$site"/*/; do
        name="$(basename "$config")"
        [[ -d "$config" && "$name" != ga ]] || continue
        checked=$((checked + 1))
        # `read` returns non-zero on a final line with no newline, having
        # populated the array — so this must not treat that as failure.
        args=()
        read -r -a args < "$WORK/$site/$name.args" || true
        if alloy validate "${args[@]}" "$config"; then
            echo "✓ $name"
        else
            echo "✗ $name"
            fail=1
        fi
        stripped="$WORK/$site/ga/$name"
        [[ -d "$stripped" ]] || continue
        if alloy validate "$stripped"; then
            echo "✓ $name without its wal blocks, at generally-available"
        else
            echo "✗ $name without its wal blocks, at generally-available:" \
                 "only loki.write's WAL may need experimental"
            fail=1
        fi
    done
done

echo "CHECKED $checked Alloy configs"
exit $fail
