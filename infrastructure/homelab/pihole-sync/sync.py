#!/usr/bin/env python3
"""
Pi-hole v6 configuration sync.

Reconciles Pi-hole state against pihole-config.yaml: adds items that are
missing and removes items that are no longer in the config. Groups are
removed last to avoid referential conflicts. A gravity update is triggered
whenever adlists are added or removed.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import yaml

PIHOLE_URL = os.environ["PIHOLE_URL"]
PIHOLE_PASSWORD = os.environ.get("PIHOLE_PASSWORD", "")
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/sync/pihole-config.yaml")

# Pi-hole's built-in Default group must never be removed.
_PROTECTED_GROUPS = {"Default"}


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


def authenticate():
    status, resp = _request("POST", "/api/auth", {"password": PIHOLE_PASSWORD})
    if status not in (200, 201):
        raise RuntimeError(f"Auth failed ({status}): {resp}")
    session = resp.get("session", {})
    if not session.get("valid"):
        raise RuntimeError(f"Auth returned invalid session: {resp}")
    sid = session.get("sid") or ""
    print(f"Authenticated (session valid={session.get('valid')}, "
          f"sid={'<none>' if not sid else sid[:8] + '...'})")
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
            print(f"  WARNING: unknown group '{name}'", file=sys.stderr)
    return ids


def sync_groups(cfg, sid):
    """
    Add missing groups. Removal of extra groups happens in remove_extra_groups()
    after all other resources have been reconciled, to avoid referential conflicts.
    Returns name→id mapping (includes all existing groups, e.g. Default).
    """
    status, resp = _request("GET", "/api/groups", sid=sid)
    if status != 200:
        raise RuntimeError(f"GET /api/groups failed ({status}): {resp}")

    name_to_id = {g["name"]: g["id"] for g in resp.get("groups", [])}
    print(f"Existing groups: {list(name_to_id.keys())}")

    for grp in cfg.get("groups", []):
        name = grp["name"]
        if name in name_to_id:
            print(f"  group '{name}' already exists (id={name_to_id[name]}), skipping")
            continue
        status, resp = _request("POST", "/api/groups", {"name": name, "enabled": True}, sid=sid)
        if status in (200, 201) and "group" in resp:
            name_to_id[name] = resp["group"]["id"]
            print(f"  created group '{name}' (id={name_to_id[name]})")
        elif status == 409:
            _, resp2 = _request("GET", "/api/groups", sid=sid)
            name_to_id = {g["name"]: g["id"] for g in resp2.get("groups", [])}
            print(f"  group '{name}' already existed (race)")
        else:
            print(f"  ERROR creating group '{name}' ({status}): {resp}", file=sys.stderr)

    return name_to_id


def remove_extra_groups(cfg, sid, name_to_id):
    """
    Remove groups that are in Pi-hole but not in the config.
    Protected groups (Default) are never removed.
    Called after all other resources are reconciled.
    """
    desired = {grp["name"] for grp in cfg.get("groups", [])} | _PROTECTED_GROUPS
    extras = {name for name in name_to_id if name not in desired}
    if not extras:
        return
    for name in extras:
        status, resp = _request("DELETE", f"/api/groups/{urllib.parse.quote(name)}", sid=sid)
        if status in (200, 204):
            print(f"  removed group '{name}'")
        else:
            print(f"  ERROR removing group '{name}' ({status}): {resp}", file=sys.stderr)


def sync_lists(cfg, sid, name_to_id, list_type, cfg_key):
    """
    Add missing adlists and remove extras. Returns (added, removed) booleans.
    Both trigger a gravity update in the caller.
    """
    status, resp = _request("GET", f"/api/lists?type={list_type}", sid=sid)
    if status != 200:
        raise RuntimeError(f"GET /api/lists?type={list_type} failed ({status}): {resp}")

    existing = {lst["address"]: lst["id"] for lst in resp.get("lists", [])}
    desired = {entry["url"] for entry in cfg.get(cfg_key, [])}
    print(f"Existing {list_type}lists: {len(existing)}, desired: {len(desired)}")

    added = False
    for entry in cfg.get(cfg_key, []):
        url = entry["url"]
        if url in existing:
            print(f"  {list_type}list exists, skipping")
            continue
        groups = _resolve_group_ids(entry.get("groups", []), name_to_id)
        status, resp = _request("POST", "/api/lists", {
            "address": url,
            "type": list_type,
            "enabled": True,
            "groups": groups,
            "comment": entry.get("comment", ""),
        }, sid=sid)
        if status in (200, 201) and "list" in resp:
            print(f"  added {list_type}list: {url}")
            added = True
        elif status == 409:
            print(f"  {list_type}list already existed (race): {url}")
        else:
            print(f"  ERROR adding {list_type}list ({status}): {url}\n    {resp}",
                  file=sys.stderr)

    removed = False
    for url, lst_id in existing.items():
        if url not in desired:
            status, resp = _request("DELETE", f"/api/lists/{lst_id}", sid=sid)
            if status in (200, 204):
                print(f"  removed {list_type}list: {url}")
                removed = True
            else:
                print(f"  ERROR removing {list_type}list id={lst_id} ({status}): {resp}",
                      file=sys.stderr)

    return added, removed


def sync_domains(cfg, sid, name_to_id, domain_type, cfg_key):
    """Add missing domains and remove extras."""
    status, resp = _request("GET", f"/api/domains/{domain_type}/exact", sid=sid)
    if status != 200:
        raise RuntimeError(
            f"GET /api/domains/{domain_type}/exact failed ({status}): {resp}")

    existing = {d["domain"]: d["id"] for d in resp.get("domains", [])}
    desired = {entry["domain"] for entry in cfg.get(cfg_key, [])}
    print(f"Existing {domain_type} domains: {len(existing)}, desired: {len(desired)}")

    for entry in cfg.get(cfg_key, []):
        domain = entry["domain"]
        if domain in existing:
            print(f"  {domain_type} domain exists, skipping: {domain}")
            continue
        groups = _resolve_group_ids(entry.get("groups", []), name_to_id)
        status, resp = _request("POST", f"/api/domains/{domain_type}/exact", {
            "domain": domain,
            "enabled": True,
            "groups": groups,
            "comment": entry.get("comment", ""),
        }, sid=sid)
        if status in (200, 201) and "domain" in resp:
            print(f"  added {domain_type} domain: {domain}")
        elif status == 409:
            print(f"  {domain_type} domain already existed (race): {domain}")
        else:
            print(f"  ERROR adding {domain_type} domain '{domain}' ({status}): {resp}",
                  file=sys.stderr)

    for domain, domain_id in existing.items():
        if domain not in desired:
            status, resp = _request(
                "DELETE", f"/api/domains/{domain_type}/exact/{domain_id}", sid=sid)
            if status in (200, 204):
                print(f"  removed {domain_type} domain: {domain}")
            else:
                print(f"  ERROR removing {domain_type} domain '{domain}' ({status}): {resp}",
                      file=sys.stderr)


def sync_clients(cfg, sid, name_to_id):
    """Add missing clients and remove extras."""
    status, resp = _request("GET", "/api/clients", sid=sid)
    if status != 200:
        raise RuntimeError(f"GET /api/clients failed ({status}): {resp}")

    existing = {c["client"] for c in resp.get("clients", [])}
    desired = {entry["address"] for entry in cfg.get("clients", [])}
    print(f"Existing clients: {len(existing)}, desired: {len(desired)}")

    for entry in cfg.get("clients", []):
        address = entry["address"]
        if address in existing:
            print(f"  client exists, skipping: {address}")
            continue
        groups = _resolve_group_ids(entry.get("groups", []), name_to_id)
        status, resp = _request("POST", "/api/clients", {
            "client": address,
            "groups": groups,
            "comment": entry.get("comment", ""),
        }, sid=sid)
        if status in (200, 201) and "client" in resp:
            print(f"  added client: {address} ({entry.get('comment', '')})")
        elif status == 409:
            print(f"  client already existed (race): {address}")
        else:
            print(f"  ERROR adding client '{address}' ({status}): {resp}", file=sys.stderr)

    for address in existing:
        if address not in desired:
            status, resp = _request(
                "DELETE", f"/api/clients/{urllib.parse.quote(address, safe='')}", sid=sid)
            if status in (200, 204):
                print(f"  removed client: {address}")
            else:
                print(f"  ERROR removing client '{address}' ({status}): {resp}",
                      file=sys.stderr)


def run_gravity(sid):
    """Trigger a gravity update and stream its output. Blocks until complete."""
    print("Triggering gravity update (this may take several minutes)...")
    url = f"{PIHOLE_URL}/api/action/gravity"
    headers = {"Content-Type": "application/json"}
    if sid:
        headers["X-FTL-SID"] = sid
    req = urllib.request.Request(url, data=b"", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            while True:
                line = resp.readline()
                if not line:
                    break
                print(f"  gravity: {line.decode('utf-8', errors='replace').rstrip()}",
                      flush=True)
        print("Gravity update complete.")
    except Exception as exc:
        print(f"Gravity update error: {exc}", file=sys.stderr)
        raise


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    print(f"Connecting to Pi-hole at {PIHOLE_URL}")
    sid = authenticate()

    # Groups: add missing now; remove extras after other resources are reconciled
    # so that group references in lists/domains/clients are cleaned up first.
    name_to_id = sync_groups(cfg, sid)

    list_added, list_removed = sync_lists(cfg, sid, name_to_id, "block", "block_lists")
    la, lr = sync_lists(cfg, sid, name_to_id, "allow", "allow_lists")
    list_added |= la
    list_removed |= lr

    sync_domains(cfg, sid, name_to_id, "allow", "allow_domains")
    sync_domains(cfg, sid, name_to_id, "deny", "block_domains")
    sync_clients(cfg, sid, name_to_id)

    remove_extra_groups(cfg, sid, name_to_id)

    if list_added or list_removed:
        run_gravity(sid)
    else:
        print("No adlist changes — skipping gravity update.")

    print("Sync complete.")


if __name__ == "__main__":
    main()
