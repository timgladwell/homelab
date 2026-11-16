"""Making Pi-hole match the config: groups, lists, domains and clients.

This is the one module that knows both the config (pihole-config.yaml and the
site's clients file) and Pi-hole (through `pihole_client`). For each kind of
object it reads what Pi-hole has, adds what is missing, regroups what has
drifted, and removes what the config no longer names. Groups are removed last,
so nothing still refers to one when it goes.

Reads and writes fail differently, on purpose:
  - A read that fails raises: nothing can be reconciled against a state that
    couldn't be read, and guessing it as empty would delete everything.
  - A write Pi-hole refuses (a 4xx) is recorded as an error and the run goes
    on, so one bad object doesn't stop the rest.
  - A write that fails because Pi-hole is out of reach, after the client's
    retries, raises: every write after it would fail the same way, slowly.
"""

import dataclasses
from collections.abc import Callable, Mapping
from http import HTTPStatus
from typing import Any

from logs import log
from pihole_client import AdList, Client, Domain, DomainType, ListType, PiholeClient, PiholeError
from results import Action, Error, Kind, Object, SyncResult, classify

# Deleting Default detaches every list, domain and client from their group.
_PROTECTED_GROUPS = {"Default"}

# Where each type lives in the config, and the `Kind` it is reported as.
_LISTS = {ListType.BLOCK: ("block_lists", Kind.BLOCK), ListType.ALLOW: ("allow_lists", Kind.ALLOW)}
_DOMAINS = {DomainType.ALLOW: ("allow_domains", Kind.ALLOW), DomainType.DENY: ("block_domains", Kind.BLOCK)}

type Entry = Mapping[str, Any]  # one item of the config, such as {"url": ..., "groups": [...]}


class Reconciler:
    """Makes one Pi-hole match one config, adding what it does to a `SyncResult`."""

    def __init__(self, pihole: PiholeClient, config: Mapping[str, Any], result: SyncResult):
        """Set up a reconciliation; nothing is read or written until `run`.

        Args:
            pihole: A logged-in client.
            config: The loaded config, with the site's clients under `clients`.
            result: The run's result. Changes and errors are added as they
                happen, so they are reported even if a later step raises.
        """
        self._pihole = pihole
        self._config = config
        self._result = result
        self._group_ids: dict[str, int] = {}

    def run(self) -> None:
        """Reconcile everything, in order. Raises `PiholeError` as the module docstring says."""
        self.sync_groups()
        self.sync_lists(ListType.BLOCK)
        self.sync_lists(ListType.ALLOW)
        self.sync_domains(DomainType.ALLOW)
        self.sync_domains(DomainType.DENY)
        self.sync_clients()
        self.remove_extra_groups()

    # ------------------------------------------------------------------
    # Each kind of object
    # ------------------------------------------------------------------

    def sync_groups(self) -> None:
        """Create the groups the config names that Pi-hole doesn't have.

        The ids are then read back from Pi-hole rather than taken from each
        answer, so a group someone else created at the same moment (a 409) is
        known by its real id too.
        """
        log("--- Syncing groups ---")
        existing = {group.name for group in self._pihole.groups()}
        log(f"Existing: {sorted(existing)}")
        for entry in self._config.get("groups", []):
            name = entry["name"]
            if name in existing:
                continue
            if self._write(f"adding group '{name}'", self._pihole.add_group, name, exists_ok=True):
                log(f"added group '{name}'")
                self._result.change(Object.GROUP, Action.ADDED)
        self._group_ids = {group.name: group.id for group in self._pihole.groups()}

    def sync_lists(self, list_type: ListType) -> None:
        """Add, regroup and remove the adlists of one type.

        Gravity is required if a list is added or removed, or if a wanted list
        has 0 domains loaded (a previous rebuild didn't complete for it). A
        regroup alone doesn't need it: Pi-hole joins a list's groups at query
        time, so the change applies at once.
        """
        log(f"--- Syncing {list_type}lists ---")
        key, kind = _LISTS[list_type]
        have = {adlist.address: adlist for adlist in self._pihole.lists(list_type)}
        want = {entry["url"]: entry for entry in self._config.get(key, [])}
        log(f"Existing: {len(have)}, desired: {len(want)}")
        for address in have.keys() & want.keys():
            if have[address].domain_count == 0:
                log(f"{list_type}list has 0 domains loaded (gravity incomplete): {address}", warn=True)
                self._result.gravity_required = True
        if self._converge(
            Object.LIST,
            kind,
            have,
            want,
            add=lambda address, groups, comment: self._pihole.add_list(address, list_type, groups, comment),
            update=self._pihole.update_list,
            delete=lambda address: self._pihole.delete_list(address, list_type),
        ):
            self._result.gravity_required = True

    def sync_domains(self, domain_type: DomainType) -> None:
        """Add, regroup and remove the exact domains of one type."""
        log(f"--- Syncing {domain_type} domains ---")
        key, kind = _DOMAINS[domain_type]
        have = {domain.domain: domain for domain in self._pihole.domains(domain_type)}
        want = {entry["domain"]: entry for entry in self._config.get(key, [])}
        log(f"Existing: {len(have)}, desired: {len(want)}")
        self._converge(
            Object.DOMAIN,
            kind,
            have,
            want,
            add=lambda domain, groups, comment: self._pihole.add_domain(domain, domain_type, groups, comment),
            update=self._pihole.update_domain,
            delete=lambda domain: self._pihole.delete_domain(domain, domain_type),
        )

    def sync_clients(self) -> None:
        """Add, regroup and remove the clients, which are keyed by address."""
        log("--- Syncing clients ---")
        have = {client.address: client for client in self._pihole.clients()}
        want = {entry["address"]: entry for entry in self._config.get("clients", [])}
        log(f"Existing: {len(have)}, desired: {len(want)}")
        self._converge(
            Object.CLIENT,
            None,
            have,
            want,
            add=self._pihole.add_client,
            update=self._pihole.update_client,
            delete=self._pihole.delete_client,
        )

    def remove_extra_groups(self) -> None:
        """Remove the groups the config doesn't name, except the protected ones."""
        log("--- Removing extra groups ---")
        wanted = {entry["name"] for entry in self._config.get("groups", [])} | _PROTECTED_GROUPS
        for name in sorted(self._group_ids.keys() - wanted):
            if self._write(f"removing group '{name}'", self._pihole.delete_group, name):
                log(f"removed group '{name}'")
                self._result.change(Object.GROUP, Action.REMOVED)

    # ------------------------------------------------------------------
    # Shared by every kind
    # ------------------------------------------------------------------

    def _converge(
        self,
        obj: Object,
        kind: Kind | None,
        have: Mapping[str, AdList | Domain | Client],
        want: Mapping[str, Entry],
        *,
        add: Callable[[str, tuple[int, ...], str], None],
        update: Callable[[Any], None],
        delete: Callable[[str], None],
    ) -> bool:
        """Make one kind of object in Pi-hole match the config.

        Pi-hole only takes group membership in an object's own body, so an
        object that already exists keeps the groups it was created with until
        it is written back: that is the regroup.

        Args:
            obj: What kind of object, for the change count and the log.
            kind: Allow or block, or None for clients.
            have: What Pi-hole has, by key (address, domain or URL).
            want: What the config wants, by the same key.
            add: Creates one, given its key, group ids and comment.
            update: Writes back an existing one, changed.
            delete: Removes one, given its key.

        Returns:
            True when an object was added or removed.
        """
        label = f"{kind} {obj}" if kind else str(obj)
        added_or_removed = False
        for key, entry in want.items():
            groups = self._group_ids_for(entry)
            comment = entry.get("comment", "")
            current = have.get(key)
            if current is None:
                if self._write(f"adding {label} '{key}'", add, key, groups, comment, exists_ok=True):
                    log(f"added {label}: {key}")
                    self._result.change(obj, Action.ADDED, kind)
                    added_or_removed = True
            elif sorted(current.groups) != sorted(groups):
                changed = dataclasses.replace(current, groups=groups, comment=comment)
                if self._write(f"regrouping {label} '{key}'", update, changed):
                    log(f"updated groups for {label} {key}: {sorted(current.groups)} -> {sorted(groups)}")
                    self._result.change(obj, Action.UPDATED, kind)
        for key in sorted(have.keys() - want.keys()):
            if self._write(f"removing {label} '{key}'", delete, key):
                log(f"removed {label}: {key}")
                self._result.change(obj, Action.REMOVED, kind)
                added_or_removed = True
        return added_or_removed

    def _write(self, description: str, call: Callable[..., object], *args: Any, exists_ok: bool = False) -> bool:
        """Make one change to Pi-hole, deciding what a failure means.

        Args:
            description: What the change is, such as "adding block domain 'x'".
            call: The client method that makes the change.
            *args: What to call it with.
            exists_ok: True for an add, where a 409 means something else
                created it first: no change, and no error.

        Returns:
            True when Pi-hole made the change.

        Raises:
            PiholeError: When Pi-hole is out of reach after the client's retries.
        """
        try:
            call(*args)
            return True
        except PiholeError as exc:
            if exc.transient:
                raise
            if exists_ok and exc.status == HTTPStatus.CONFLICT:
                log(f"{description}: it already existed (created at the same time)")
                return False
            self._result.add_error(
                Error(
                    classify(exc),
                    f"{description} failed: {exc}",
                    context={"method": exc.method, "route": exc.route, "status": exc.status},
                )
            )
            return False

    def _group_ids_for(self, entry: Entry) -> tuple[int, ...]:
        """The ids of the groups a config entry names.

        A name Pi-hole doesn't have is skipped with a warning: a typo in the
        config shouldn't stop the sync.
        """
        ids = []
        for name in entry.get("groups", []):
            if name in self._group_ids:
                ids.append(self._group_ids[name])
            else:
                log(f"unknown group '{name}'", warn=True, group=name)
        return tuple(ids)
