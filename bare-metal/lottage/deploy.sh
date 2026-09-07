#!/usr/bin/env bash
# Render Lottage's stack from this repo and push it to the Pi.
#
# Runs here, not there: no repo checkout and no git credentials on the box.
# The Pi holds exactly one thing this repo does not — the Cloudflare API token
# in /etc/lottage/cloudflare.ini.
#
# Update flow is `git pull && ./bare-metal/lottage/deploy.sh`. Everything the
# clusters changed comes with it, because the files below are read out of
# base/ rather than copied into bare-metal/.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="$REPO_ROOT/bare-metal/lottage"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

cd "$REPO_ROOT"

python3 - "$SRC" "$BUILD" <<'PY'
import pathlib
import re
import sys

import yaml

src, build = (pathlib.Path(p) for p in sys.argv[1:3])
repo = src.parent.parent

# ---------------------------------------------------------------
# Variables, in Flux's own order: network-vars first (what the estate is),
# then lottage.env over the top (what this site is). Same precedence as
# postBuild.substituteFrom, so a value means here what it means there.
# ---------------------------------------------------------------
variables = yaml.safe_load((repo / "clusters/common/network-vars.yaml").read_text())["data"]
for line in (src / "lottage.env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#"):
        key, _, value = line.partition("=")
        variables[key] = value

# base/dns/dnsmasq-base.conf points the site wildcard at ${METALLB_TRAEFIK_IP}.
# There is no MetalLB and no Traefik here; nginx on the Pi itself plays both
# parts, so the name resolves to the same box.
variables["METALLB_TRAEFIK_IP"] = variables["LOTTAGE_HOST_IP"]

placeholder = [k for k, v in variables.items() if v == "CHANGEME"]
if placeholder:
    sys.exit(f"lottage.env still has placeholders: {', '.join(sorted(placeholder))}")


def render(text: str) -> str:
    for key, value in variables.items():
        text = text.replace("${%s}" % key, str(value))
    return text


def write(rel: str, text: str) -> None:
    out = build / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)


# ---------------------------------------------------------------
# PiHole's environment, lifted out of the cluster's Deployment rather than
# restated here. This is the load-bearing reuse: without it every FTLCONF_*
# change made for Akron and Eastbank silently skips Lottage, which is the
# drift this whole arrangement exists to avoid.
# ---------------------------------------------------------------
deployment = yaml.safe_load((repo / "base/dns/pihole-deployment.yaml").read_text())
containers = deployment["spec"]["template"]["spec"]["containers"]
env = {e["name"]: e.get("value", "") for c in containers if c["name"] == "pihole" for e in c["env"]}

# The one override. See the divergence note in the runbook: recursion costs
# 2-4 upstream queries plus the DNSSEC chain per cold name, forwarding costs
# one, and this site is on DSL.
env["FTLCONF_dns_upstreams"] = "1.1.1.1;1.0.0.1"

write("pihole.env", render("".join(f"{k}={v}\n" for k, v in env.items())))

# ---------------------------------------------------------------
# The rest: this site's own files, plus the shared ones read from base/.
# ---------------------------------------------------------------
for rel, source in {
    "nginx.conf": src / "nginx.conf",
    "alloy.alloy": src / "alloy.alloy",
    "dnsmasq.d/dnsmasq-base.conf": repo / "base/dns/dnsmasq-base.conf",
    "dnsmasq.d/site.conf": src / "site.conf",
    "sync/pihole-config.yaml": repo / "base/pihole-sync/pihole-config.yaml",
    "sync/pihole-clients.yaml": src / "pihole-clients.yaml",
}.items():
    write(rel, render(source.read_text()))

# sync.py is copied, never rendered: it is a program, and ${...} inside one
# would be Python, not a Flux placeholder.
write("sync/sync.py", (repo / "base/pihole-sync/sync.py").read_text())

# compose.yaml is copied for the same reason in reverse — its ${...} is
# compose's own interpolation, resolved on the Pi from the .env beside it.
write("compose.yaml", (src / "compose.yaml").read_text())
write(".env", "".join(f"{k}={v}\n" for k, v in variables.items()))

# ---------------------------------------------------------------
# The landing page, minus the parts that only exist on a cluster. Lottage's
# reverse proxy is nginx, which has no dashboard, so the Traefik link would
# resolve, present a valid certificate and 404.
# ---------------------------------------------------------------
html = (repo / "base/landing/index.html").read_text()
html = re.sub(r"(?s)<li[^>]*data-k8s-only.*?</li>\s*", "", html)
write("html/index.html", render(html))

# ---------------------------------------------------------------
# Validation step 7's invariant, which does not reach these files: a surviving
# ${...} means an undefined variable, and it would be applied verbatim.
# ---------------------------------------------------------------
for path in sorted(build.rglob("*")):
    if path.is_file() and path.name not in ("compose.yaml", ".env"):
        if "${" in path.read_text():
            sys.exit(f"unsubstituted variable left in {path.relative_to(build)}")

print(f"rendered {sum(1 for p in build.rglob('*') if p.is_file())} files")
PY

# Validation step 12's job. Only runs if alloy is installed locally
# (`brew install grafana/grafana/alloy`) — the pipeline cannot do this one,
# because it reads Alloy configs out of a site's built k8s output.
if command -v alloy > /dev/null; then
    alloy validate "$BUILD/alloy.alloy"
else
    echo "WARNING: alloy not installed, skipping config validation"
fi

# shellcheck disable=SC1091
SSH_HOST="$(grep -E '^LOTTAGE_SSH=' "$SRC/lottage.env" | cut -d= -f2)"

rsync -a --delete "$BUILD/" "$SSH_HOST:/opt/lottage/"
ssh "$SSH_HOST" 'cd /opt/lottage && docker compose up -d && docker compose run --rm pihole-sync'
