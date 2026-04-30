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
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml

PIHOLE_URL = os.environ["PIHOLE_URL"]
PIHOLE_PASSWORD = os.environ.get("PIHOLE_PASSWORD", "")
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/sync/pihole-config.yaml")
STATE_DIR = os.environ.get("STATE_DIR", "/tmp/state")
_GRAVITY_FLAG = os.path.join(STATE_DIR, "gravity.flag")

_PROTECTED_GROUPS = {"Default"}
_MAX_RETRIES = 5
_RETRY_BASE_DELAY = 2.0  # seconds; doubles on each attempt


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(msg, *, err=False):
    print(f"[{_ts()}] {msg}", file=sys.stderr if err else sys.stdout, flush=True)


# ---------------------------------------------------------------------------
# Gravity flag (persists across container restarts within a pod via emptyDir)
# ---------------------------------------------------------------------------

def _set_gravity_flag():
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_GRAVITY_FLAG, "w") as f:
        f.write("1")
    log("  Gravity flag written to disk.")


def _clear_gravity_flag():
    try:
        os.remove(_GRAVITY_FLAG)
        log("  Gravity flag cleared.")
    except FileNotFoundError:
        pass


def _gravity_flag_set():
    return os.path.exists(_GRAVITY_FLAG)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _request(method, path, body=None, sid=None, timeout=60):
    url = f"{PIHOLE_URL}{path}"
    headers = {"Content-Type": "application/json"}
    if sid:
        headers["X-FTL-SID"] = sid
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"error": raw}
    # urllib.error.URLError propagates — _request_with_retry handles it


def _request_with_retry(method, path, body=None, sid=None, timeout=60):
    """Call _request with exponential backoff on network errors and 5xx/429."""
    last_exc = None
    for attempt in range(_MAX_RETRIES + 1):
        if attempt:
            delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
            log(f"  Retrying {method} {path} in {delay:.0f}s "
                f"(attempt {attempt + 1}/{_MAX_RETRIES + 1})...")
            time.sleep(delay)
        try:
            status, resp = _request(method, path, body=body, sid=sid, timeout=timeout)
        except urllib.error.URLError as exc:
            log(f"  {method} {path} network error: {exc}", err=True)
            last_exc = exc
            continue
        if status == 429 or status >= 500:
            log(f"  {method} {path} transient error ({status}): {resp}", err=True)
            last_exc = RuntimeError(f"HTTP {status}: {resp}")
            continue
        return status, resp
    if last_exc:
        raise last_exc
    raise RuntimeError(f"{method} {path} failed after {_MAX_RETRIES + 1} attempts")


def authenticate():
    log(f"Authenticating with Pi-hole at {PIHOLE_URL}...")
    status, resp = _request_with_retry("POST", "/api/auth", {"password": PIHOLE_PASSWORD})
    if status not in (200, 201):
        raise RuntimeError(f"Auth failed ({status}): {resp}")
    session = resp.get("session", {})
    if not session.get("valid"):
        raise RuntimeError(f"Auth returned invalid session: {resp}")
    sid = session.get("sid") or ""
    log(f"Authenticated (sid={'<none>' if not sid else sid[:8] + '...'}, "
        f"valid={session.get('valid')})")
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
            log(f"  WARNING: unknown group '{name}'", err=True)
    return ids


def sync_groups(cfg, sid):
    log("--- Syncing groups ---")
    status, resp = _request_with_retry("GET", "/api/groups", sid=sid)
    if status != 200:
        raise RuntimeError(f"GET /api/groups failed ({status}): {resp}")

    name_to_id = {g["name"]: g["id"] for g in resp.get("groups", [])}
    log(f"  Existing: {sorted(name_to_id.keys())}")

    for grp in cfg.get("groups", []):
        name = grp["name"]
        if name in name_to_id:
            log(f"  group '{name}' exists (id={name_to_id[name]})")
            continue
        status, resp = _request_with_retry(
            "POST", "/api/groups", {"name": name, "enabled": True}, sid=sid)
        if status in (200, 201) and "group" in resp:
            name_to_id[name] = resp["group"]["id"]
            log(f"  created group '{name}' (id={name_to_id[name]})")
        elif status == 409:
            _, resp2 = _request_with_retry("GET", "/api/groups", sid=sid)
            name_to_id = {g["name"]: g["id"] for g in resp2.get("groups", [])}
            log(f"  group '{name}' already existed (race)")
        else:
            log(f"  ERROR creating group '{name}' ({status}): {resp}", err=True)

    return name_to_id


def remove_extra_groups(cfg, sid, name_to_id):
    log("--- Removing extra groups ---")
    desired = {grp["name"] for grp in cfg.get("groups", [])} | _PROTECTED_GROUPS
    extras = {name for name in name_to_id if name not in desired}
    if not extras:
        log("  No extra groups.")
        return
    for name in extras:
        status, resp = _request_with_retry(
            "DELETE", f"/api/groups/{urllib.parse.quote(name)}", sid=sid)
        if status in (200, 204):
            log(f"  removed group '{name}'")
        else:
            log(f"  ERROR removing group '{name}' ({status}): {resp}", err=True)


def sync_lists(cfg, sid, name_to_id, list_type, cfg_key):
    """
    Add/remove adlists. Returns True if a gravity update is needed.

    Gravity is needed if any list is added or removed, or if any desired list
    has 0 domains loaded (gravity was not completed for that list previously).
    """
    log(f"--- Syncing {list_type}lists ---")
    status, resp = _request_with_retry("GET", f"/api/lists?type={list_type}", sid=sid)
    if status != 200:
        raise RuntimeError(f"GET /api/lists?type={list_type} failed ({status}): {resp}")

    # Store full list object so we can check domain counts and IDs.
    existing = {lst["address"]: lst for lst in resp.get("lists", [])}
    desired = {entry["url"] for entry in cfg.get(cfg_key, [])}
    log(f"  Existing: {len(existing)}, desired: {len(desired)}")

    gravity_needed = False

    # Any desired list with 0 domains means gravity didn't complete for it previously.
    for url, lst in existing.items():
        if url in desired and lst.get("number", -1) == 0:
            log(f"  {list_type}list has 0 domains loaded (gravity incomplete): {url}")
            gravity_needed = True

    # Add missing lists.
    for entry in cfg.get(cfg_key, []):
        url = entry["url"]
        if url in existing:
            lst = existing[url]
            log(f"  {list_type}list exists "
                f"(id={lst['id']}, domains={lst.get('number', '?')}): {url}")
            continue
        groups = _resolve_group_ids(entry.get("groups", []), name_to_id)
        status, resp = _request_with_retry("POST", "/api/lists", {
            "address": url,
            "type": list_type,
            "enabled": True,
            "groups": groups,
            "comment": entry.get("comment", ""),
        }, sid=sid)
        if status in (200, 201) and "list" in resp:
            log(f"  added {list_type}list: {url}")
            gravity_needed = True
        elif status == 409:
            log(f"  {list_type}list already existed (race): {url}")
        else:
            log(f"  ERROR adding {list_type}list ({status}): {url} — {resp}", err=True)

    # Remove lists no longer in config.
    for url, lst in existing.items():
        if url not in desired:
            status, resp = _request_with_retry(
                "DELETE", f"/api/lists/{lst['id']}", sid=sid)
            if status in (200, 204):
                log(f"  removed {list_type}list (id={lst['id']}): {url}")
                gravity_needed = True
            else:
                log(f"  ERROR removing {list_type}list id={lst['id']} ({status}): {resp}",
                    err=True)

    return gravity_needed


def sync_domains(cfg, sid, name_to_id, domain_type, cfg_key):
    log(f"--- Syncing {domain_type} domains ---")
    status, resp = _request_with_retry(
        "GET", f"/api/domains/{domain_type}/exact", sid=sid)
    if status != 200:
        raise RuntimeError(
            f"GET /api/domains/{domain_type}/exact failed ({status}): {resp}")

    existing = {d["domain"]: d["id"] for d in resp.get("domains", [])}
    desired = {entry["domain"] for entry in cfg.get(cfg_key, [])}
    log(f"  Existing: {len(existing)}, desired: {len(desired)}")

    for entry in cfg.get(cfg_key, []):
        domain = entry["domain"]
        if domain in existing:
            log(f"  {domain_type} domain exists (id={existing[domain]}): {domain}")
            continue
        groups = _resolve_group_ids(entry.get("groups", []), name_to_id)
        status, resp = _request_with_retry(
            "POST", f"/api/domains/{domain_type}/exact", {
                "domain": domain,
                "enabled": True,
                "groups": groups,
                "comment": entry.get("comment", ""),
            }, sid=sid)
        if status in (200, 201) and "domain" in resp:
            log(f"  added {domain_type} domain: {domain}")
        elif status == 409:
            log(f"  {domain_type} domain already existed (race): {domain}")
        else:
            log(f"  ERROR adding {domain_type} domain '{domain}' ({status}): {resp}", err=True)

    for domain, domain_id in existing.items():
        if domain not in desired:
            status, resp = _request_with_retry(
                "DELETE", f"/api/domains/{domain_type}/exact/{domain_id}", sid=sid)
            if status in (200, 204):
                log(f"  removed {domain_type} domain: {domain}")
            else:
                log(f"  ERROR removing {domain_type} domain '{domain}' ({status}): {resp}",
                    err=True)


def sync_clients(cfg, sid, name_to_id):
    log("--- Syncing clients ---")
    status, resp = _request_with_retry("GET", "/api/clients", sid=sid)
    if status != 200:
        raise RuntimeError(f"GET /api/clients failed ({status}): {resp}")

    existing = {c["client"] for c in resp.get("clients", [])}
    desired = {entry["address"] for entry in cfg.get("clients", [])}
    log(f"  Existing: {len(existing)}, desired: {len(desired)}")

    for entry in cfg.get("clients", []):
        address = entry["address"]
        if address in existing:
            log(f"  client exists: {address} ({entry.get('comment', '')})")
            continue
        groups = _resolve_group_ids(entry.get("groups", []), name_to_id)
        status, resp = _request_with_retry("POST", "/api/clients", {
            "client": address,
            "groups": groups,
            "comment": entry.get("comment", ""),
        }, sid=sid)
        if status in (200, 201) and "client" in resp:
            log(f"  added client: {address} ({entry.get('comment', '')})")
        elif status == 409:
            log(f"  client already existed (race): {address}")
        else:
            log(f"  ERROR adding client '{address}' ({status}): {resp}", err=True)

    for address in existing:
        if address not in desired:
            status, resp = _request_with_retry(
                "DELETE",
                f"/api/clients/{urllib.parse.quote(address, safe='')}",
                sid=sid)
            if status in (200, 204):
                log(f"  removed client: {address}")
            else:
                log(f"  ERROR removing client '{address}' ({status}): {resp}", err=True)


def run_gravity(sid):
    log("--- Running gravity update (may take several minutes) ---")
    t0 = time.monotonic()
    url = f"{PIHOLE_URL}/api/action/gravity"
    headers = {"Content-Type": "application/json"}
    if sid:
        headers["X-FTL-SID"] = sid
    req = urllib.request.Request(url, data=b"", headers=headers, method="POST")
    try:
        # timeout=300: allow up to 5 minutes of silence between streamed lines.
        # Individual list fetches from external sources can be slow.
        with urllib.request.urlopen(req, timeout=300) as resp:
            while True:
                line = resp.readline()
                if not line:
                    break
                log(f"  gravity: {line.decode('utf-8', errors='replace').rstrip()}")
        elapsed = time.monotonic() - t0
        log(f"Gravity update complete ({elapsed:.0f}s).")
    except Exception as exc:
        elapsed = time.monotonic() - t0
        log(f"Gravity update failed after {elapsed:.0f}s: {exc}", err=True)
        raise


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    t_start = time.monotonic()
    log(f"Pi-hole sync starting. Config={CONFIG_PATH} URL={PIHOLE_URL}")

    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    log(f"Config summary: {len(cfg.get('groups', []))} groups, "
        f"{len(cfg.get('clients', []))} clients, "
        f"{len(cfg.get('allow_domains', []))} allow_domains, "
        f"{len(cfg.get('block_domains', []))} block_domains, "
        f"{len(cfg.get('allow_lists', []))} allow_lists, "
        f"{len(cfg.get('block_lists', []))} block_lists")

    sid = authenticate()

    name_to_id = sync_groups(cfg, sid)

    # Check whether a previous container restart in this pod already set the flag.
    gravity_needed = _gravity_flag_set()
    if gravity_needed:
        log("Gravity flag present from previous attempt — gravity will run.")

    gn = sync_lists(cfg, sid, name_to_id, "block", "block_lists")
    gravity_needed |= gn
    gn = sync_lists(cfg, sid, name_to_id, "allow", "allow_lists")
    gravity_needed |= gn

    sync_domains(cfg, sid, name_to_id, "allow", "allow_domains")
    sync_domains(cfg, sid, name_to_id, "deny", "block_domains")
    sync_clients(cfg, sid, name_to_id)

    remove_extra_groups(cfg, sid, name_to_id)

    if gravity_needed:
        # Write the flag before starting gravity so that if gravity fails and
        # the container is restarted, the next run will re-attempt gravity even
        # though no list changes are detected.
        _set_gravity_flag()
        run_gravity(sid)
        _clear_gravity_flag()
    else:
        log("No gravity-triggering changes — skipping gravity update.")

    elapsed = time.monotonic() - t_start
    log(f"Sync complete ({elapsed:.0f}s).")


if __name__ == "__main__":
    main()
