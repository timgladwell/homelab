#!/usr/bin/env python3
"""Check that every chart we install still moves the software it deploys.

Every other check passes when a chart source stops tracking its app: the chart
renders, Renovate PRs keep arriving (or stop, which looks the same), and a
`flate diff` showing only a version string reads as harmless. That happened
twice at once (#533): grafana/loki re-scoped to Enterprise and froze its OSS
image, and Unpoller's chart moved to OCI and left its HTTP index behind.

  check <built.yaml> <rendered.yaml>
      Validation step 15, per site. Fails a release when its chart is marked
      deprecated, or when none of the images it renders carries the chart's
      appVersion (the Loki case) and the tag is not pinned in its values.
      A pinned tag is deliberate: step 09 requires each one to carry a
      `# renovate:` comment Renovate matches, or a `# renovate-skip: <reason>`
      that it prints on every run.

  audit <build-dir>
      Weekly, from validate.yml's schedule; it needs the network beyond chart
      metadata, and its findings are about upstream, not about a PR. Reads the
      k3s-built-<site>.yaml / k3s-rendered-<site>.yaml that steps 03 and 15
      leave in <build-dir>, and fails when:
        - a chart source redirects (the Unpoller case: its HTTP index moved),
        - the newest chart in a source is deprecated,
        - a source looks frozen: its newest chart is older than FROZEN_DAYS
          and the app's image has a newer stable release than that chart's
          appVersion. Both, because a chart that releases rarely is fine as
          long as the app has not moved past it,
        - a `# renovate: datasource=docker` pin has no source label and no
          `sourceUrl` packageRule, so its Renovate PRs carry no release notes
          and reviewing them means leaving the PR (#535).

Network: chart metadata comes from `helm show chart`, and the audit reads
registries anonymously. Findings print one per line, prefixed ✗.
"""
import datetime
import json
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FROZEN_DAYS = 60
UA = {"User-Agent": "homelab-chart-sources"}  # GitHub Pages refuses urllib's default
MANIFEST_TYPES = ",".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])


# --- pure helpers (tests/test_chart_sources.py) ---------------------------

def version(s):
    """(major, minor, patch) from a tag or appVersion, or None. Lenient: v-prefix and suffixes ignored."""
    m = re.match(r"v?(\d+)\.(\d+)(?:\.(\d+))?", str(s or ""))
    return tuple(int(x or 0) for x in m.groups()) if m else None


def stable(tag):
    return re.fullmatch(r"v?\d+\.\d+\.\d+", tag) is not None


def split_image(ref):
    """'traefik:v3' -> ('docker.io', 'library/traefik', 'v3'); digests dropped."""
    ref = ref.split("@", 1)[0]
    name, tag = ref, ""
    if ":" in ref.rsplit("/", 1)[-1]:
        name, tag = ref.rsplit(":", 1)
    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, repo = first, rest
    else:
        registry, repo = "docker.io", name
    if registry == "docker.io" and "/" not in repo:
        repo = "library/" + repo
    return registry, repo, tag


def images(obj):
    """Every string under an `image` key, anywhere in a rendered object."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "image" and isinstance(v, str):
                yield v
            else:
                yield from images(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from images(v)


def values_tags(obj):
    """Every `tag:` string in a HelmRelease's values: tags pinned on purpose."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "tag" and isinstance(v, (str, int, float)):
                yield str(v)
            else:
                yield from values_tags(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from values_tags(v)


def judge(app_version, rendered_images, pinned):
    """The image carrying appVersion, else a pinned one, else None (a finding)."""
    want = version(app_version)
    for img in sorted(rendered_images):
        if want and version(split_image(img)[2]) == want:
            return img, "carries appVersion"
    for img in sorted(rendered_images):
        if split_image(img)[2] in pinned:
            return img, "tag pinned in values"
    return None, None


def index_entries(text, chart):
    """One chart's entries from a repo index.yaml, without parsing the whole multi-MB file."""
    # Helm writes each chart's list at the same indent as its key, so the
    # block ends at the next two-space key that is not a `- ` item.
    m = re.search(rf"^  {re.escape(chart)}:\n(.*?)(?=^  [^\s-]|^\S|\Z)", text, re.M | re.S)
    return (yaml.safe_load(m.group(1)) or []) if m else []


def bearer_params(header):
    return dict(re.findall(r'(\w+)="([^"]*)"', header or ""))


# --- inputs ---------------------------------------------------------------

def load(path):
    with open(path) as f:
        return [d for d in yaml.safe_load_all(f) if isinstance(d, dict)]


def releases(built_docs, rendered_docs):
    """[(release, repo spec, chart name, deployed chart version, rendered images)]."""
    repos = {(d["metadata"].get("namespace"), d["metadata"]["name"]): d["spec"]
             for d in built_docs if d.get("kind") == "HelmRepository"}
    rendered = {}
    for d in rendered_docs:
        labels = (d.get("metadata") or {}).get("labels") or {}
        key = (labels.get("helm.toolkit.fluxcd.io/namespace"), labels.get("helm.toolkit.fluxcd.io/name"))
        r = rendered.setdefault(key, {"charts": set(), "images": set()})
        # kube-prometheus-stack still uses the pre-Helm-3 `chart` label.
        r["charts"].update(labels[k] for k in ("helm.sh/chart", "chart") if k in labels)
        r["images"].update(images(d))
    out = []
    for hr in (d for d in built_docs if d.get("kind") == "HelmRelease"):
        ns, name = hr["metadata"].get("namespace"), hr["metadata"]["name"]
        spec = hr["spec"]["chart"]["spec"]
        src = spec["sourceRef"]
        repo = repos[(src.get("namespace", ns), src["name"])]
        r = rendered.get((ns, name), {"charts": set(), "images": set()})
        prefix = spec["chart"] + "-"
        deployed = next((c[len(prefix):] for c in r["charts"] if c.startswith(prefix) and version(c[len(prefix):])), None)
        out.append((hr, repo, spec["chart"], deployed, r["images"]))
    return out


def show_chart(repo, chart, ver=None):
    if repo.get("type") == "oci":
        ref = [f"{repo['url'].rstrip('/')}/{chart}"]
    else:
        ref = [chart, "--repo", repo["url"]]
    cmd = ["helm", "show", "chart", *ref] + (["--version", ver] if ver else [])
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if p.returncode:
        raise RuntimeError(f"{' '.join(cmd)}: {p.stderr.strip()}")
    return yaml.safe_load(p.stdout)


def cached(fn):
    memo = {}
    def wrapper(*args):
        key = json.dumps(args, sort_keys=True, default=str)
        if key not in memo:
            memo[key] = fn(*args)
        return memo[key]
    return wrapper


show_chart = cached(show_chart)


# --- check (step 15) ------------------------------------------------------

def check(built, rendered):
    rels = releases(load(built), load(rendered))
    with ThreadPoolExecutor(8) as pool:
        metas = list(pool.map(lambda r: show_chart(r[1], r[2], r[3]) if r[3] else None, rels))
    failed = 0
    for (hr, repo, chart, deployed, imgs), meta in zip(rels, metas):
        rid = f"{hr['metadata'].get('namespace')}/{hr['metadata']['name']}"
        if meta is None:
            print(f"  ✗ {rid}: no rendered object carries a helm.sh/chart (or chart) label for {chart}")
            failed += 1
            continue
        if meta.get("deprecated"):
            print(f"  ✗ {rid}: {chart}-{deployed} is marked deprecated by its publisher; "
                  "find where the chart moved (#533)")
            failed += 1
            continue
        img, why = judge(meta.get("appVersion"), imgs, set(values_tags(hr["spec"].get("values"))))
        if img:
            print(f"  ✓ {rid}: {chart}-{deployed} appVersion {meta.get('appVersion')}; {img} ({why})")
        else:
            print(f"  ✗ {rid}: {chart}-{deployed} says appVersion {meta.get('appVersion')}, but renders none of it: "
                  f"{', '.join(sorted(imgs)) or 'no images'}. The chart may have stopped tracking its app (#533): "
                  "find what does, or pin the tag in values with a `# renovate:` comment")
            failed += 1
    print(f"  {len(rels)} releases checked against their chart metadata")
    return failed


# --- audit (weekly) -------------------------------------------------------

def http(url, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=60)


def registry_get(registry, repo, path, accept="application/json"):
    """GET /v2/<repo>/<path> anonymously, answering a Bearer challenge if there is one."""
    host = "registry-1.docker.io" if registry == "docker.io" else registry
    url = f"https://{host}/v2/{repo}/{path}"
    try:
        return http(url, {"Accept": accept})
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise
        p = bearer_params(e.headers.get("WWW-Authenticate"))
        q = urllib.parse.urlencode({k: v for k, v in (("service", p.get("service")),
                                                      ("scope", f"repository:{repo}:pull")) if v})
        token = json.load(http(f"{p['realm']}?{q}"))
        token = token.get("token") or token.get("access_token")
        return http(url, {"Accept": accept, "Authorization": f"Bearer {token}"})


@cached
def newest_stable_tag(registry, repo):
    tags, path = [], "tags/list?n=1000"
    while path:
        r = registry_get(registry, repo, path)
        tags += json.load(r).get("tags") or []
        link = r.headers.get("Link", "")
        m = re.search(r"<[^>]*/v2/[^?]*\?([^>]*)>", link)
        path = f"tags/list?{m.group(1)}" if m else None
    tags = [t for t in tags if stable(t)]
    return max(tags, key=version) if tags else None


@cached
def source_label(registry, repo, tag):
    idx = json.load(registry_get(registry, repo, f"manifests/{tag}", MANIFEST_TYPES))
    man = idx
    if "manifests" in idx:
        d = next((m for m in idx["manifests"] if (m.get("platform") or {}).get("architecture") == "arm64"),
                 idx["manifests"][0])
        man = json.load(registry_get(registry, repo, f"manifests/{d['digest']}", MANIFEST_TYPES))
    cfg = json.load(registry_get(registry, repo, f"blobs/{man['config']['digest']}", "*/*")).get("config") or {}
    labels = cfg.get("Labels") or {}
    return labels.get("org.opencontainers.image.source") or labels.get("org.label-schema.vcs-url")


@cached
def newest_chart(repo, chart):
    """(version, appVersion, created date or None, redirected-to URL or None)."""
    if repo.get("type") == "oci":
        registry, _, base = repo["url"].removeprefix("oci://").partition("/")
        ocirepo = f"{base}/{chart}"
        tags = [t for t in json.load(registry_get(registry, ocirepo, "tags/list")).get("tags", []) if version(t)]
        ver = max(tags, key=version)
        man = json.load(registry_get(registry, ocirepo, f"manifests/{ver}", MANIFEST_TYPES))
        created = (man.get("annotations") or {}).get("org.opencontainers.image.created")
        meta = show_chart(repo, chart, ver)
        return ver, meta.get("appVersion"), created, None
    url = repo["url"].rstrip("/") + "/index.yaml"
    r = http(url)
    moved = r.geturl() if r.geturl() != url else None
    entries = [e for e in index_entries(r.read().decode(), chart) if not re.search(r"-", str(e.get("version")))]
    e = max(entries, key=lambda e: version(e["version"]) or (0,))
    return str(e["version"]), e.get("appVersion"), str(e.get("created") or "") or None, moved


def age_days(stamp):
    when = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    return (datetime.datetime.now(datetime.timezone.utc) - when).days


def audit(build_dir):
    findings, seen = [], set()

    def finding(msg):
        print(f"  ✗ {msg}")
        findings.append(msg)

    builds = sorted(pathlib.Path(build_dir).glob("k3s-built-*.yaml"))
    if not builds:  # validation failed before step 3 wrote anything; not a clean audit
        finding(f"no k3s-built-<site>.yaml in {build_dir}; run validate-k3s.sh with K3S_BUILD_DIR set first")
    for built in builds:
        site = built.name.removeprefix("k3s-built-").removesuffix(".yaml")
        rendered = built.with_name(f"k3s-rendered-{site}.yaml")
        print(f"--- {site} ---")
        for hr, repo, chart, deployed, imgs in releases(load(built), load(rendered)):
            key = (repo["url"], chart)
            if key in seen:
                continue
            seen.add(key)
            rid = f"{chart} from {repo['url']}"
            try:
                ver, app, created, moved = newest_chart(repo, chart)
                if moved:
                    finding(f"{rid}: index redirects to {moved}; check the chart is still released there (#534)")
                if show_chart(repo, chart, ver).get("deprecated"):
                    finding(f"{rid}: newest chart {ver} is deprecated")
                meta = show_chart(repo, chart, deployed)
                img, _ = judge(meta.get("appVersion"), imgs, set(values_tags(hr["spec"].get("values"))))
                if not img:
                    print(f"  ~ {rid}: no main image identified (step 15 reports this)")
                    continue
                registry, irepo, _ = split_image(img)
                upstream = newest_stable_tag(registry, irepo)
                age = age_days(created) if created else None
                frozen = (upstream and version(upstream) > (version(app) or (0,))
                          and (age is None or age > FROZEN_DAYS))
                state = f"newest chart {ver} (appVersion {app}, {age if age is not None else '?'}d old); " \
                        f"newest {registry}/{irepo} is {upstream}"
                if frozen and age is not None:
                    finding(f"{rid}: looks frozen: {state}")
                elif frozen:
                    finding(f"{rid}: cannot date the newest chart, and the app has moved past it: {state}")
                else:
                    print(f"  ✓ {rid}: {state}")
            except Exception as e:  # one unreachable source must not hide the others
                finding(f"{rid}: could not audit: {e}")

    print("--- renovate docker pins ---")
    rules = json.loads((REPO_ROOT / "renovate.json").read_text()).get("packageRules", [])
    with_url = {n for r in rules if r.get("sourceUrl") for n in r.get("matchDepNames", [])}
    pins = set()
    for f in sorted(REPO_ROOT.rglob("*.y*ml")):
        if ".git" in f.parts or ".claude" in f.parts:
            continue
        pins.update(re.findall(r"# renovate: datasource=docker depName=(\S+)\s+\S+: (\S+)", f.read_text(errors="replace")))
    for dep, value in sorted(pins):
        registry, repo, _ = split_image(dep)
        tag = split_image(value)[2] or value
        try:
            label = source_label(registry, repo, tag)
        except Exception as e:
            finding(f"{dep}: could not read its labels: {e}")
            continue
        if label or dep in with_url:
            print(f"  ✓ {dep}: release notes from {label or 'a renovate.json sourceUrl rule'}")
        else:
            finding(f"{dep}: no org.opencontainers.image.source label, so Renovate PRs carry no release notes; "
                    "add a packageRules sourceUrl for it in renovate.json")
    return len(findings)


if __name__ == "__main__":
    if sys.argv[1:2] == ["check"] and len(sys.argv) == 4:
        sys.exit(1 if check(sys.argv[2], sys.argv[3]) else 0)
    if sys.argv[1:2] == ["audit"] and len(sys.argv) == 3:
        sys.exit(1 if audit(sys.argv[2]) else 0)
    sys.exit(__doc__)
