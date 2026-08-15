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
# this file.
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

    # Substitution is what Flux's postBuild does at reconcile time; without it
    # every ${VAR} is a parse error and this step would fail on every config.
    # Step 7 is what catches a variable that has no definition.
    python3 - "$BUILD_OUTPUT" "clusters/${site}/cluster-vars.yaml" "$WORK/$site" <<'PY'
import pathlib, sys, yaml

built, vars_file, outdir = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
cluster_vars = yaml.safe_load(pathlib.Path(vars_file).read_text())["data"]

for doc in yaml.safe_load_all(pathlib.Path(built).read_text()):
    if not doc or doc.get("kind") != "HelmRelease":
        continue
    alloy = doc.get("spec", {}).get("values", {}).get("alloy", {})
    content = alloy.get("configMap", {}).get("content")
    if not content:
        continue
    for key, value in cluster_vars.items():
        content = content.replace("${%s}" % key, str(value))
    name = doc["metadata"]["name"]
    (outdir / f"{name}.alloy").write_text(content)

    # Alloy refuses to run a component below the process's stability level, so
    # a release that has not opted in must not validate as though it had.
    # Akron's log collector passes --stability.level=public-preview and uses
    # otelcol.receiver.syslog; the metrics collectors pass nothing and must
    # stay on generally-available components.
    flags = [a for a in alloy.get("extraArgs", [])
             if a.startswith("--stability.level")]
    (outdir / f"{name}.args").write_text(" ".join(flags))
PY

    for config in "$WORK/$site"/*.alloy; do
        [[ -f "$config" ]] || continue
        checked=$((checked + 1))
        name="$(basename "$config" .alloy)"
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
    done
done

echo "CHECKED $checked Alloy configs"
exit $fail
