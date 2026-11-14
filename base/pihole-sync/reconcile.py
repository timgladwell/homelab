"""Making Pi-hole match the config: groups, lists, domains and clients.

Each `sync_*` function reads what Pi-hole has, adds what is missing, removes what
is no longer in the config, and returns a `Result`: what it changed, and every
per-object error. An error on one object doesn't stop the rest. Problems that
stop the run (Pi-hole answering a read with an error) raise `SyncError` instead.
Groups are removed last, to avoid referential conflicts.
"""

import urllib.parse

import pihole_api as api
from logs import log
from results import ErrorCode, Result, SyncError

_PROTECTED_GROUPS = {"Default"}
_DOMAIN_KIND = {"allow": "allow", "deny": "block"}  # Pi-hole's name -> ours


def _resolve_group_ids(names, name_to_id):
    """Turn group names into the ids Pi-hole's API takes.

    Args:
        names: Group names from the config.
        name_to_id: `{group name: id}` for the groups Pi-hole has.

    Returns:
        The ids, in the order of `names`. A name Pi-hole doesn't have is
        skipped with a warning: a typo in the config shouldn't stop the sync.
    """
    ids = []
    for name in names:
        if name in name_to_id:
            ids.append(name_to_id[name])
        else:
            log(f"unknown group '{name}'", warn=True, group=name)
    return ids


def _fail(result, message, method, path, status, resp):
    """Record a per-object failure: the run goes on, and ends as a failure.

    Args:
        result: The `Result` to record it on.
        message: What failed, such as "adding domain 'x' failed".
        method: The HTTP method of the request that failed.
        path: The path of the request that failed.
        status: The HTTP status Pi-hole answered with.
        resp: Pi-hole's response body, kept to 500 characters in the log.
    """
    result.fail(ErrorCode.PIHOLE_API, message, method=method, path=path, status=status, response=str(resp)[:500])


def _reconcile_groups(existing, entry, name_to_id, path, payload, sid, label, obj, kind=None):
    """Rewrite an existing object when its group membership drifted from config.

    Pi-hole only accepts group membership in an object's own body, so an object
    that already exists keeps whatever groups it was created with until it is
    explicitly PUT back.

    Args:
        existing: The object as Pi-hole has it, with its current `groups` (ids).
        entry: The object's entry in the config, with the group names it wants.
        name_to_id: `{group name: id}` for the groups Pi-hole has.
        path: The API path of the object, for the PUT.
        payload: The body to PUT, without its `groups`, which are filled in here.
        sid: The Pi-hole session id.
        label: Names the object in the log, such as "blocklist http://...".
        obj: The kind of object, for the change count: "list", "domain" or
            "client".
        kind: "allow" or "block" for a domain or a list, and None for a client.

    Returns:
        A `Result` with one "updated" change if the PUT was sent and accepted, an
        error if it was refused, and nothing if the groups already matched.
    """
    result = Result()
    desired = _resolve_group_ids(entry.get("groups", []), name_to_id)
    current = existing.get("groups", [])
    if sorted(current) == sorted(desired):
        return result
    payload["groups"] = desired
    status, resp = api.request_with_retry("PUT", path, payload, sid=sid)
    if status in (200, 201):
        log(f"updated groups for {label}: {sorted(current)} -> {sorted(desired)}")
        result.change(obj, "updated", kind)
    else:
        _fail(result, f"updating groups for {label} failed", "PUT", path, status, resp)
    return result


def sync_groups(cfg, sid):
    """Create the groups the config names that Pi-hole doesn't have.

    Args:
        cfg: The config, loaded from pihole-config.yaml (and the site's clients
            file), as a dict.
        sid: The Pi-hole session id from `pihole_api.authenticate`.

    Returns:
        A tuple of `({group name: id}, Result)`: every group Pi-hole now has,
        and what was created or failed. The mapping is what the other
        reconcilers use to turn group names into ids.

    Raises:
        SyncError: If Pi-hole refuses to list its groups, because nothing else
            can be reconciled without them.
    """
    log("--- Syncing groups ---")
    result = Result()
    status, resp = api.request_with_retry("GET", "/api/groups", sid=sid)
    if status != 200:
        raise SyncError(ErrorCode.PIHOLE_API, f"GET /api/groups failed ({status}): {resp}")

    name_to_id = {g["name"]: g["id"] for g in resp.get("groups", [])}
    log(f"Existing: {sorted(name_to_id.keys())}")

    for grp in cfg.get("groups", []):
        name = grp["name"]
        if name in name_to_id:
            log(f"group '{name}' exists (id={name_to_id[name]})")
            continue
        status, resp = api.request_with_retry("POST", "/api/groups", {"name": name, "enabled": True}, sid=sid)
        if status in (200, 201) and ("group" in resp or "groups" in resp):
            name_to_id[name] = resp["group"]["id"] if "group" in resp else resp["groups"][0]["id"]
            log(f"created group '{name}' (id={name_to_id[name]})")
            result.change("group", "added")
        elif status == 409:
            _, resp2 = api.request_with_retry("GET", "/api/groups", sid=sid)
            name_to_id = {g["name"]: g["id"] for g in resp2.get("groups", [])}
            log(f"group '{name}' already existed (race)")
        else:
            _fail(result, f"creating group '{name}' failed", "POST", "/api/groups", status, resp)

    return name_to_id, result


def remove_extra_groups(cfg, sid, name_to_id):
    """Remove the groups Pi-hole has that the config doesn't name.

    This runs last, so nothing still refers to a group when it goes. The
    protected groups (Default) are never removed.

    Args:
        cfg: The config, loaded from pihole-config.yaml (and the site's clients
            file), as a dict.
        sid: The Pi-hole session id from `pihole_api.authenticate`.
        name_to_id: `{group name: id}` for the groups Pi-hole has.

    Returns:
        A `Result` with one "removed" change per group, or an error for each one
        Pi-hole refused to remove.
    """
    log("--- Removing extra groups ---")
    result = Result()
    desired = {grp["name"] for grp in cfg.get("groups", [])} | _PROTECTED_GROUPS
    extras = {name for name in name_to_id if name not in desired}
    if not extras:
        log("No extra groups.")
        return result
    for name in extras:
        path = f"/api/groups/{urllib.parse.quote(name)}"
        status, resp = api.request_with_retry("DELETE", path, sid=sid)
        if status in (200, 204):
            log(f"removed group '{name}'")
            result.change("group", "removed")
        else:
            _fail(result, f"removing group '{name}' failed", "DELETE", path, status, resp)
    return result


def sync_lists(cfg, sid, name_to_id, list_type, cfg_key):
    """Add and remove adlists, and fix drifted group membership.

    Gravity is needed if any list is added or removed, or if any desired list
    has 0 domains loaded (gravity was not completed for that list previously).
    A group change is not enough: `vw_gravity` joins adlist_by_group at query
    time, so regrouping an already-downloaded list applies immediately.

    Args:
        cfg: The config, loaded from pihole-config.yaml (and the site's clients
            file), as a dict.
        sid: The Pi-hole session id from `pihole_api.authenticate`.
        name_to_id: `{group name: id}` for the groups Pi-hole has.
        list_type: "block" or "allow", which kind of list this call syncs.
        cfg_key: The config key holding the desired lists, such as "block_lists".

    Returns:
        A `Result` with each list added, removed or regrouped, any per-list
        errors, and `gravity_needed` set when the lists need a gravity rebuild.

    Raises:
        SyncError: If Pi-hole refuses to list its lists.
    """
    log(f"--- Syncing {list_type}lists ---")
    result = Result()
    status, resp = api.request_with_retry("GET", f"/api/lists?type={list_type}", sid=sid)
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
        status, resp = api.request_with_retry(
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
            status, resp = api.request_with_retry("DELETE", path, sid=sid)
            if status in (200, 204):
                log(f"removed {list_type}list (id={lst['id']}): {url}")
                result.change("list", "removed", list_type)
                result.gravity_needed = True
            else:
                _fail(result, f"removing {list_type}list id={lst['id']} failed", "DELETE", path, status, resp)

    return result


def sync_domains(cfg, sid, name_to_id, domain_type, cfg_key):
    """Add, remove and regroup the exact domains of one type.

    Args:
        cfg: The config, loaded from pihole-config.yaml (and the site's clients
            file), as a dict.
        sid: The Pi-hole session id from `pihole_api.authenticate`.
        name_to_id: `{group name: id}` for the groups Pi-hole has.
        domain_type: Pi-hole's name for the type: "allow" or "deny". It is
            reported as the `kind` "allow" or "block".
        cfg_key: The config key holding the desired domains, such as
            "block_domains".

    Returns:
        A `Result` with each domain added, removed or regrouped, and any
        per-domain errors.

    Raises:
        SyncError: If Pi-hole refuses to list its domains.
    """
    log(f"--- Syncing {domain_type} domains ---")
    result = Result()
    kind = _DOMAIN_KIND[domain_type]
    status, resp = api.request_with_retry("GET", f"/api/domains/{domain_type}/exact", sid=sid)
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
        status, resp = api.request_with_retry(
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
            status, resp = api.request_with_retry("DELETE", path, sid=sid)
            if status in (200, 204):
                log(f"removed {domain_type} domain: {domain}")
                result.change("domain", "removed", kind)
            else:
                _fail(result, f"removing {domain_type} domain '{domain}' failed", "DELETE", path, status, resp)
    return result


def sync_clients(cfg, sid, name_to_id):
    """Add, remove and regroup the clients, which are keyed by address.

    A missing clients file would look like "no clients wanted" and remove every
    client the site has, so the entry point refuses to run without one.

    Args:
        cfg: The config, loaded from pihole-config.yaml (and the site's clients
            file), as a dict.
        sid: The Pi-hole session id from `pihole_api.authenticate`.
        name_to_id: `{group name: id}` for the groups Pi-hole has.

    Returns:
        A `Result` with each client added, removed or regrouped, and any
        per-client errors.

    Raises:
        SyncError: If Pi-hole refuses to list its clients.
    """
    log("--- Syncing clients ---")
    result = Result()
    status, resp = api.request_with_retry("GET", "/api/clients", sid=sid)
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
        status, resp = api.request_with_retry(
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
            status, resp = api.request_with_retry("DELETE", path, sid=sid)
            if status in (200, 204):
                log(f"removed client: {address}")
                result.change("client", "removed")
            else:
                _fail(result, f"removing client '{address}' failed", "DELETE", path, status, resp)
    return result
