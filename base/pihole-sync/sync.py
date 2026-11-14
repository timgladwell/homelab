#!/usr/bin/env python3
"""
Pi-hole v6 configuration sync.

Reconciles Pi-hole state against pihole-config.yaml: adds items that are
missing and removes items that are no longer in the config. Groups are
removed last to avoid referential conflicts.

Gravity is triggered when:
  - Any adlist is added or removed in this run, OR
  - Any desired adlist has 0 domains loaded (indicating a prior gravity run
    did not complete for that list), OR
  - The gravity flag file exists (set by a previous container run in this pod
    before gravity completed, ensuring retries always re-attempt gravity).
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import observability as obs
import yaml
from observability import ErrorCode, Result, SyncError, error_field, log

PIHOLE_URL = os.environ["PIHOLE_URL"]
PIHOLE_PASSWORD = os.environ.get("PIHOLE_PASSWORD", "")
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/sync/pihole-config.yaml")
# Client definitions are site-specific, so they come from a separate file that
# each site's dns-config layer merges into the same ConfigMap. Deliberately
# required, not optional: a missing file would look like "no clients desired"
# and sync_clients() would delete every client the site actually has.
CLIENTS_PATH = os.environ.get("CLIENTS_PATH", "/sync/pihole-clients.yaml")
STATE_DIR = os.environ.get("STATE_DIR", "/tmp/state")
_GRAVITY_FLAG = os.path.join(STATE_DIR, "gravity.flag")

_PROTECTED_GROUPS = {"Default"}
_DOMAIN_KIND = {"allow": "allow", "deny": "block"}  # Pi-hole's name -> ours
# Named so Pi-hole's own logs attribute these calls to this syncer rather than
# to "Python-urllib", which every other script would also claim.
_USER_AGENT = "pihole-sync"
_MAX_RETRIES = 5
_RETRY_BASE_DELAY = 2.0  # seconds; doubles on each attempt


# ---------------------------------------------------------------------------
# Gravity flag (persists across container restarts within a pod via emptyDir)
# ---------------------------------------------------------------------------


def _set_gravity_flag():
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_GRAVITY_FLAG, "w") as f:
        f.write("1")
    log("Gravity flag written to disk.")


def _clear_gravity_flag():
    try:
        os.remove(_GRAVITY_FLAG)
        log("Gravity flag cleared.")
    except FileNotFoundError:
        pass


def _gravity_flag_set():
    return os.path.exists(_GRAVITY_FLAG)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _headers(sid=None):
    headers = {"Content-Type": "application/json", "User-Agent": _USER_AGENT}
    if sid:
        headers["X-FTL-SID"] = sid
    return headers


# Pi-hole paths that carry a name, as the template a metric is labelled with. A
# path itself has a domain name or a list URL in it, which would make one series
# per object; the log line keeps the full path.
_ROUTES = (
    (re.compile(r"^/api/lists/[^/?]+"), "/api/lists/{url}"),
    (re.compile(r"^/api/domains/(allow|deny)/exact/[^/?]+"), r"/api/domains/\1/exact/{domain}"),
    (re.compile(r"^/api/groups/[^/?]+"), "/api/groups/{name}"),
    (re.compile(r"^/api/clients/[^/?]+"), "/api/clients/{address}"),
)


def _route(path):
    for pattern, template in _ROUTES:
        if pattern.match(path):
            return pattern.sub(template, path.split("?")[0])
    return path.split("?")[0]


def _request(method, path, body=None, sid=None, timeout=60):
    url = f"{PIHOLE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(sid), method=method)
    with obs.observed_request(method, path, _route(path)) as request:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                request.status = resp.status
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            request.status = e.code
            with e:  # closes the response; an HTTPError holds the connection open
                raw = e.read().decode(errors="replace")
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"error": raw}
        # urllib.error.URLError propagates — _request_with_retry handles it


def _retry(attempt):
    """The `retry` part of an error line: omitted once no attempts are left."""
    if attempt >= _MAX_RETRIES:
        return None
    return {"iteration": attempt + 1, "max_iterations": _MAX_RETRIES + 1}


def _request_with_retry(method, path, body=None, sid=None, timeout=60):
    """Call _request with exponential backoff on network errors and 5xx/429."""
    last_exc = None
    for attempt in range(_MAX_RETRIES + 1):
        if attempt:
            delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
            log(
                f"Retrying {method} {path} in {delay:.0f}s (attempt {attempt + 1}/{_MAX_RETRIES + 1})...",
                method=method,
                path=path,
            )
            time.sleep(delay)
        try:
            status, resp = _request(method, path, body=body, sid=sid, timeout=timeout)
        except urllib.error.URLError as exc:
            log(
                "Request failed: network error",
                warn=True,
                method=method,
                path=path,
                **error_field(ErrorCode.NETWORK, str(exc), exception=type(exc).__name__, retry=_retry(attempt)),
            )
            last_exc = exc
            continue
        if status == 429 or status >= 500:
            message = f"HTTP {status}: {resp}"
            log(
                "Request failed: transient error",
                warn=True,
                method=method,
                path=path,
                status=status,
                **error_field(ErrorCode.PIHOLE_API, message, retry=_retry(attempt)),
            )
            last_exc = SyncError(ErrorCode.PIHOLE_API, message)
            continue
        return status, resp
    if last_exc:
        raise last_exc
    raise SyncError(ErrorCode.PIHOLE_API, f"{method} {path} failed after {_MAX_RETRIES + 1} attempts")


def authenticate():
    log(f"Checking auth requirements at {PIHOLE_URL}...")
    status, resp = _request_with_retry("GET", "/api/auth")
    session = resp.get("session", {})
    if session.get("valid") and not session.get("sid"):
        log(f"{session.get('message', 'no password set')} — proceeding without a session.")
        return ""

    log("Authenticating with Pi-hole...")
    status, resp = _request_with_retry("POST", "/api/auth", {"password": PIHOLE_PASSWORD})
    if status not in (200, 201):
        raise SyncError(ErrorCode.AUTH, f"Auth failed ({status}): {resp}")
    session = resp.get("session", {})
    if not session.get("valid"):
        raise SyncError(ErrorCode.AUTH, f"Auth returned invalid session: {resp}")
    sid = session.get("sid") or ""
    log(f"Authenticated (sid={'<none>' if not sid else sid[:8] + '...'}, valid={session.get('valid')})")
    return sid


# ---------------------------------------------------------------------------
# Sync functions
# ---------------------------------------------------------------------------


def _resolve_group_ids(names, name_to_id):
    ids = []
    for name in names:
        if name in name_to_id:
            ids.append(name_to_id[name])
        else:
            log(f"unknown group '{name}'", warn=True, group=name)
    return ids


def _fail(result, message, method, path, status, resp):
    """Record a per-object failure: the run goes on, and ends as a failure."""
    result.fail(ErrorCode.PIHOLE_API, message, method=method, path=path, status=status, response=str(resp)[:500])


def _reconcile_groups(existing, entry, name_to_id, path, payload, sid, label, obj, kind=None):
    """
    Rewrite an existing object when its group membership drifted from config.

    Pi-hole only accepts group membership in an object's own body, so an object
    that already exists keeps whatever groups it was created with until it is
    explicitly PUT back. Returns the Result of the PUT, empty if none was needed.
    """
    result = Result()
    desired = _resolve_group_ids(entry.get("groups", []), name_to_id)
    current = existing.get("groups", [])
    if sorted(current) == sorted(desired):
        return result
    payload["groups"] = desired
    status, resp = _request_with_retry("PUT", path, payload, sid=sid)
    if status in (200, 201):
        log(f"updated groups for {label}: {sorted(current)} -> {sorted(desired)}")
        result.change(obj, "updated", kind)
    else:
        _fail(result, f"updating groups for {label} failed", "PUT", path, status, resp)
    return result


def sync_groups(cfg, sid):
    """Returns ({group name: id}, Result)."""
    log("--- Syncing groups ---")
    result = Result()
    status, resp = _request_with_retry("GET", "/api/groups", sid=sid)
    if status != 200:
        raise SyncError(ErrorCode.PIHOLE_API, f"GET /api/groups failed ({status}): {resp}")

    name_to_id = {g["name"]: g["id"] for g in resp.get("groups", [])}
    log(f"Existing: {sorted(name_to_id.keys())}")

    for grp in cfg.get("groups", []):
        name = grp["name"]
        if name in name_to_id:
            log(f"group '{name}' exists (id={name_to_id[name]})")
            continue
        status, resp = _request_with_retry("POST", "/api/groups", {"name": name, "enabled": True}, sid=sid)
        if status in (200, 201) and ("group" in resp or "groups" in resp):
            name_to_id[name] = resp["group"]["id"] if "group" in resp else resp["groups"][0]["id"]
            log(f"created group '{name}' (id={name_to_id[name]})")
            result.change("group", "added")
        elif status == 409:
            _, resp2 = _request_with_retry("GET", "/api/groups", sid=sid)
            name_to_id = {g["name"]: g["id"] for g in resp2.get("groups", [])}
            log(f"group '{name}' already existed (race)")
        else:
            _fail(result, f"creating group '{name}' failed", "POST", "/api/groups", status, resp)

    return name_to_id, result


def remove_extra_groups(cfg, sid, name_to_id):
    log("--- Removing extra groups ---")
    result = Result()
    desired = {grp["name"] for grp in cfg.get("groups", [])} | _PROTECTED_GROUPS
    extras = {name for name in name_to_id if name not in desired}
    if not extras:
        log("No extra groups.")
        return result
    for name in extras:
        path = f"/api/groups/{urllib.parse.quote(name)}"
        status, resp = _request_with_retry("DELETE", path, sid=sid)
        if status in (200, 204):
            log(f"removed group '{name}'")
            result.change("group", "removed")
        else:
            _fail(result, f"removing group '{name}' failed", "DELETE", path, status, resp)
    return result


def sync_lists(cfg, sid, name_to_id, list_type, cfg_key):
    """
    Add/remove adlists and fix drifted group membership. Returns a Result whose
    gravity_needed says whether a gravity update is needed.

    Gravity is needed if any list is added or removed, or if any desired list
    has 0 domains loaded (gravity was not completed for that list previously).
    A group change is not enough: `vw_gravity` joins adlist_by_group at query
    time, so regrouping an already-downloaded list applies immediately.
    """
    log(f"--- Syncing {list_type}lists ---")
    result = Result()
    status, resp = _request_with_retry("GET", f"/api/lists?type={list_type}", sid=sid)
    if status != 200:
        raise SyncError(ErrorCode.PIHOLE_API, f"GET /api/lists?type={list_type} failed ({status}): {resp}")

    # Store full list object so we can check domain counts and IDs.
    existing = {lst["address"]: lst for lst in resp.get("lists", [])}
    desired = {entry["url"] for entry in cfg.get(cfg_key, [])}
    log(f"Existing: {len(existing)}, desired: {len(desired)}")

    # Any desired list with 0 domains means gravity didn't complete for it previously.
    for url, lst in existing.items():
        if url in desired and lst.get("number", -1) == 0:
            log(f"{list_type}list has 0 domains loaded (gravity incomplete): {url}", warn=True)
            result.gravity_needed = True

    # Add missing lists.
    for entry in cfg.get(cfg_key, []):
        url = entry["url"]
        if url in existing:
            lst = existing[url]
            log(f"{list_type}list exists (id={lst['id']}, domains={lst.get('number', '?')}): {url}")
            # No gravity: the domains are already in the `gravity` table
            # tagged with this adlist_id, and `vw_gravity` resolves group
            # membership by joining adlist_by_group at query time. Rewriting
            # that join takes effect immediately.
            result.merge(
                _reconcile_groups(
                    lst,
                    entry,
                    name_to_id,
                    f"/api/lists/{urllib.parse.quote(url, safe='')}?type={list_type}",
                    {
                        "address": url,
                        "enabled": lst.get("enabled", True),
                        "comment": entry.get("comment", ""),
                    },
                    sid,
                    f"{list_type}list {url}",
                    "list",
                    list_type,
                )
            )
            continue
        groups = _resolve_group_ids(entry.get("groups", []), name_to_id)
        path = f"/api/lists?type={list_type}"
        status, resp = _request_with_retry(
            "POST",
            path,
            {
                "address": url,
                "enabled": True,
                "groups": groups,
                "comment": entry.get("comment", ""),
            },
            sid=sid,
        )
        if status in (200, 201):
            if "list" not in resp and "lists" not in resp:
                log(f"added {list_type}list but response missing 'list'/'lists': {resp}", warn=True)
            log(f"added {list_type}list: {url}")
            result.change("list", "added", list_type)
            result.gravity_needed = True
        elif status == 409:
            log(f"{list_type}list already existed (race): {url}")
        else:
            _fail(result, f"adding {list_type}list {url} failed", "POST", path, status, resp)

    # Remove lists no longer in config.
    for url, lst in existing.items():
        if url not in desired:
            path = f"/api/lists/{urllib.parse.quote(url, safe='')}?type={list_type}"
            status, resp = _request_with_retry("DELETE", path, sid=sid)
            if status in (200, 204):
                log(f"removed {list_type}list (id={lst['id']}): {url}")
                result.change("list", "removed", list_type)
                result.gravity_needed = True
            else:
                _fail(result, f"removing {list_type}list id={lst['id']} failed", "DELETE", path, status, resp)

    return result


def sync_domains(cfg, sid, name_to_id, domain_type, cfg_key):
    log(f"--- Syncing {domain_type} domains ---")
    result = Result()
    kind = _DOMAIN_KIND[domain_type]
    status, resp = _request_with_retry("GET", f"/api/domains/{domain_type}/exact", sid=sid)
    if status != 200:
        raise SyncError(ErrorCode.PIHOLE_API, f"GET /api/domains/{domain_type}/exact failed ({status}): {resp}")

    existing = {d["domain"]: d for d in resp.get("domains", [])}
    desired = {entry["domain"] for entry in cfg.get(cfg_key, [])}
    log(f"Existing: {len(existing)}, desired: {len(desired)}")

    for entry in cfg.get(cfg_key, []):
        domain = entry["domain"]
        if domain in existing:
            dom = existing[domain]
            log(f"{domain_type} domain exists (id={dom['id']}): {domain}")
            result.merge(
                _reconcile_groups(
                    dom,
                    entry,
                    name_to_id,
                    f"/api/domains/{domain_type}/exact/{urllib.parse.quote(domain, safe='')}",
                    {
                        "domain": domain,
                        "enabled": dom.get("enabled", True),
                        "comment": entry.get("comment", ""),
                    },
                    sid,
                    f"{domain_type} domain {domain}",
                    "domain",
                    kind,
                )
            )
            continue
        groups = _resolve_group_ids(entry.get("groups", []), name_to_id)
        path = f"/api/domains/{domain_type}/exact"
        status, resp = _request_with_retry(
            "POST",
            path,
            {
                "domain": domain,
                "enabled": True,
                "groups": groups,
                "comment": entry.get("comment", ""),
            },
            sid=sid,
        )
        if status in (200, 201):
            if "domain" not in resp and "domains" not in resp:
                log(f"added {domain_type} domain but response missing 'domain'/'domains': {resp}", warn=True)
            log(f"added {domain_type} domain: {domain}")
            result.change("domain", "added", kind)
        elif status == 409:
            log(f"{domain_type} domain already existed (race): {domain}")
        else:
            _fail(result, f"adding {domain_type} domain '{domain}' failed", "POST", path, status, resp)

    for domain in existing:
        if domain not in desired:
            path = f"/api/domains/{domain_type}/exact/{urllib.parse.quote(domain, safe='')}"
            status, resp = _request_with_retry("DELETE", path, sid=sid)
            if status in (200, 204):
                log(f"removed {domain_type} domain: {domain}")
                result.change("domain", "removed", kind)
            else:
                _fail(result, f"removing {domain_type} domain '{domain}' failed", "DELETE", path, status, resp)
    return result


def sync_clients(cfg, sid, name_to_id):
    log("--- Syncing clients ---")
    result = Result()
    status, resp = _request_with_retry("GET", "/api/clients", sid=sid)
    if status != 200:
        raise SyncError(ErrorCode.PIHOLE_API, f"GET /api/clients failed ({status}): {resp}")

    existing = {c["client"]: c for c in resp.get("clients", [])}
    desired = {entry["address"] for entry in cfg.get("clients", [])}
    log(f"Existing: {len(existing)}, desired: {len(desired)}")

    for entry in cfg.get("clients", []):
        address = entry["address"]
        if address in existing:
            log(f"client exists: {address} ({entry.get('comment', '')})")
            result.merge(
                _reconcile_groups(
                    existing[address],
                    entry,
                    name_to_id,
                    f"/api/clients/{urllib.parse.quote(address, safe='')}",
                    {
                        "client": address,
                        "comment": entry.get("comment", ""),
                    },
                    sid,
                    f"client {address}",
                    "client",
                )
            )
            continue
        groups = _resolve_group_ids(entry.get("groups", []), name_to_id)
        status, resp = _request_with_retry(
            "POST",
            "/api/clients",
            {
                "client": address,
                "groups": groups,
                "comment": entry.get("comment", ""),
            },
            sid=sid,
        )
        if status in (200, 201):
            if "client" not in resp and "clients" not in resp:
                log(f"added client but response missing 'client'/'clients': {resp}", warn=True)
            log(f"added client: {address} ({entry.get('comment', '')})")
            result.change("client", "added")
        elif status == 409:
            log(f"client already existed (race): {address}")
        else:
            _fail(result, f"adding client '{address}' failed", "POST", "/api/clients", status, resp)

    for address in existing:
        if address not in desired:
            path = f"/api/clients/{urllib.parse.quote(address, safe='')}"
            status, resp = _request_with_retry("DELETE", path, sid=sid)
            if status in (200, 204):
                log(f"removed client: {address}")
                result.change("client", "removed")
            else:
                _fail(result, f"removing client '{address}' failed", "DELETE", path, status, resp)
    return result


def run_gravity(sid):
    log("--- Running gravity update (may take several minutes) ---")
    t0 = time.monotonic()
    url = f"{PIHOLE_URL}/api/action/gravity"
    req = urllib.request.Request(url, data=b"", headers=_headers(sid), method="POST")
    try:
        # timeout=300: allow up to 5 minutes of silence between streamed lines.
        # Individual list fetches from external sources can be slow.
        with urllib.request.urlopen(req, timeout=300) as resp:
            while True:
                line = resp.readline()
                if not line:
                    break
                log(f"gravity: {line.decode('utf-8', errors='replace').rstrip()}")
        elapsed = time.monotonic() - t0
        log(f"Gravity update complete ({elapsed:.0f}s).")
    except Exception as exc:
        elapsed = time.monotonic() - t0
        raise SyncError(ErrorCode.GRAVITY, f"Gravity update failed after {elapsed:.0f}s: {exc}") from exc


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _load_config():
    try:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        with open(CLIENTS_PATH) as f:
            cfg["clients"] = (yaml.safe_load(f) or {}).get("clients") or []
    except (OSError, yaml.YAMLError, TypeError) as exc:
        raise SyncError(ErrorCode.CONFIG, f"Cannot load config: {exc}") from exc
    return cfg


def _run(result):
    """Do the sync, adding to `result` as it goes, so that if a fatal error ends
    the run part-way, what was already changed is still reported."""
    log(f"Pi-hole sync starting. Config={CONFIG_PATH} Clients={CLIENTS_PATH} URL={PIHOLE_URL}")

    cfg = _load_config()

    log(
        f"Config summary: {len(cfg.get('groups', []))} groups, "
        f"{len(cfg.get('clients', []))} clients, "
        f"{len(cfg.get('allow_domains', []))} allow_domains, "
        f"{len(cfg.get('block_domains', []))} block_domains, "
        f"{len(cfg.get('allow_lists', []))} allow_lists, "
        f"{len(cfg.get('block_lists', []))} block_lists"
    )

    sid = authenticate()

    name_to_id, groups = sync_groups(cfg, sid)
    result.merge(groups)

    result.merge(sync_lists(cfg, sid, name_to_id, "block", "block_lists"))
    result.merge(sync_lists(cfg, sid, name_to_id, "allow", "allow_lists"))

    result.merge(sync_domains(cfg, sid, name_to_id, "allow", "allow_domains"))
    result.merge(sync_domains(cfg, sid, name_to_id, "deny", "block_domains"))
    result.merge(sync_clients(cfg, sid, name_to_id))

    result.merge(remove_extra_groups(cfg, sid, name_to_id))

    # A previous container restart in this pod may already have set the flag.
    if _gravity_flag_set():
        log("Gravity flag present from previous attempt — gravity will run.")
        result.gravity_needed = True

    if result.gravity_needed:
        # Write the flag before starting gravity so that if gravity fails and
        # the container is restarted, the next run will re-attempt gravity even
        # though no list changes are detected.
        _set_gravity_flag()
        run_gravity(sid)
        _clear_gravity_flag()
    else:
        log("No gravity-triggering changes — skipping gravity update.")


def main():
    with obs.observed_run() as run:
        _run(run.result)


if __name__ == "__main__":
    main()
