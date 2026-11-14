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

This file is the entry point: it loads the config and runs the steps in order.
The rest is split by responsibility, each module with its own docstring:
  pihole_api  talking to Pi-hole: requests, retries, authentication, gravity
  reconcile   making groups, lists, domains and clients match the config
  results     the outcome of a run: what changed and what went wrong
  logs        the JSON log lines
  metrics     the metrics, and the blocks that report a run and its requests
"""

import os

import pihole_api as api
import reconcile
import yaml
from logs import log
from metrics import observed_run
from results import ErrorCode, SyncError

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/sync/pihole-config.yaml")
# Client definitions are site-specific, so they come from a separate file that
# each site's dns-config layer merges into the same ConfigMap. Deliberately
# required, not optional: a missing file would look like "no clients desired"
# and reconcile.sync_clients() would delete every client the site actually has.
CLIENTS_PATH = os.environ.get("CLIENTS_PATH", "/sync/pihole-clients.yaml")
STATE_DIR = os.environ.get("STATE_DIR", "/tmp/state")
_GRAVITY_FLAG = os.path.join(STATE_DIR, "gravity.flag")


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
    log(f"Pi-hole sync starting. Config={CONFIG_PATH} Clients={CLIENTS_PATH} URL={api.PIHOLE_URL}")

    cfg = _load_config()

    log(
        f"Config summary: {len(cfg.get('groups', []))} groups, "
        f"{len(cfg.get('clients', []))} clients, "
        f"{len(cfg.get('allow_domains', []))} allow_domains, "
        f"{len(cfg.get('block_domains', []))} block_domains, "
        f"{len(cfg.get('allow_lists', []))} allow_lists, "
        f"{len(cfg.get('block_lists', []))} block_lists"
    )

    sid = api.authenticate()

    name_to_id, groups = reconcile.sync_groups(cfg, sid)
    result.merge(groups)

    result.merge(reconcile.sync_lists(cfg, sid, name_to_id, "block", "block_lists"))
    result.merge(reconcile.sync_lists(cfg, sid, name_to_id, "allow", "allow_lists"))

    result.merge(reconcile.sync_domains(cfg, sid, name_to_id, "allow", "allow_domains"))
    result.merge(reconcile.sync_domains(cfg, sid, name_to_id, "deny", "block_domains"))
    result.merge(reconcile.sync_clients(cfg, sid, name_to_id))

    result.merge(reconcile.remove_extra_groups(cfg, sid, name_to_id))

    # A previous container restart in this pod may already have set the flag.
    if _gravity_flag_set():
        log("Gravity flag present from previous attempt — gravity will run.")
        result.gravity_needed = True

    if result.gravity_needed:
        # Write the flag before starting gravity so that if gravity fails and
        # the container is restarted, the next run will re-attempt gravity even
        # though no list changes are detected.
        _set_gravity_flag()
        api.run_gravity(sid)
        _clear_gravity_flag()
    else:
        log("No gravity-triggering changes — skipping gravity update.")


def main():
    with observed_run() as run:
        _run(run.result)


if __name__ == "__main__":
    main()
