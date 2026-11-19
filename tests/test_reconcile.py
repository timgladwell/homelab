"""base/pihole-sync/reconcile.py — making Pi-hole match the config, against a fake Pi-hole.

The reconciler talks to Pi-hole only through the client's methods, so the fake
stands in for the client and no HTTP is involved.
"""

import unittest
from http import HTTPStatus

import logs
from pihole_client import AdList, Client, DomainType, Group, ListType, PiholeError
from reconcile import Reconciler
from results import Action, Change, ErrorCode, Kind, Object, SyncResult


class FakePihole:
    """A Pi-hole held in memory. `refuse` maps a method name to the status it fails with."""

    def __init__(self, groups=(), lists=(), domains=(), clients=(), refuse=None):
        self.groups_ = {g.name: g for g in groups}
        self.lists_ = list(lists)
        self.domains_ = list(domains)
        self.clients_ = list(clients)
        self.refuse = refuse or {}
        self.calls = []

    def _call(self, name, *args):
        self.calls.append((name, *args))
        if name in self.refuse:
            raise PiholeError(f"{name} refused", method="X", route="/api/x", status=self.refuse[name])

    def of(self, name):
        return [call[1:] for call in self.calls if call[0] == name]

    def groups(self):
        self._call("groups")
        return list(self.groups_.values())

    def add_group(self, name):
        self._call("add_group", name)
        self.groups_[name] = Group(len(self.groups_) + 10, name)

    def delete_group(self, name):
        self._call("delete_group", name)

    def lists(self, list_type):
        self._call("lists", list_type)
        return [lst for lst in self.lists_ if lst.type == list_type]

    def add_list(self, address, list_type, groups, comment):
        self._call("add_list", address, list_type, groups, comment)

    def update_list(self, adlist):
        self._call("update_list", adlist)

    def delete_list(self, address, list_type):
        self._call("delete_list", address, list_type)

    def domains(self, domain_type):
        self._call("domains", domain_type)
        return [d for d in self.domains_ if d.type == domain_type]

    def add_domain(self, domain, domain_type, groups, comment):
        self._call("add_domain", domain, domain_type, groups, comment)

    def update_domain(self, domain):
        self._call("update_domain", domain)

    def delete_domain(self, domain, domain_type):
        self._call("delete_domain", domain, domain_type)

    def clients(self):
        self._call("clients")
        return list(self.clients_)

    def add_client(self, address, groups, comment):
        self._call("add_client", address, groups, comment)

    def update_client(self, client):
        self._call("update_client", client)

    def delete_client(self, address):
        self._call("delete_client", address)


GROUPS = (Group(0, "Default"), Group(7, "Extra"))


def reconcile(pihole, config):
    """Run a whole reconciliation; return its result.

    The config wants the groups in GROUPS unless it says otherwise, so a test
    about something else doesn't also remove a group.
    """
    config.setdefault("groups", [{"name": "Extra"}])
    result = SyncResult()
    Reconciler(pihole, config, result).run()
    return result


class Quiet(unittest.TestCase):
    def setUp(self):
        logs.logger.disabled = True  # the reconciler logs a running commentary

    def tearDown(self):
        logs.logger.disabled = False


class Lists(Quiet):
    """The failure that prompted the original self-check: an existing list whose
    groups drifted must be written back, and that alone must not trigger the
    ~10 minute gravity rebuild."""

    def setUp(self):
        super().setUp()
        self.pihole = FakePihole(
            groups=GROUPS,
            lists=[
                AdList("http://a", ListType.BLOCK, groups=(0,), domain_count=5),
                AdList("http://b", ListType.BLOCK, groups=(0,), domain_count=5),
            ],
        )
        self.config = {
            "block_lists": [
                {"url": "http://a", "groups": ["Default", "Extra"]},
                {"url": "http://b", "groups": ["Default"]},
            ]
        }

    def test_regroups_only_the_drifted_list_without_gravity(self):
        result = reconcile(self.pihole, self.config)
        (update,) = self.pihole.of("update_list")
        self.assertEqual((update[0].address, sorted(update[0].groups)), ("http://a", [0, 7]))
        self.assertEqual(result.changes, {Change(Object.LIST, Action.UPDATED, Kind.BLOCK): 1})
        self.assertFalse(result.gravity_required)

    def test_no_drift_means_no_writes(self):
        self.config["block_lists"][0]["groups"] = ["Default"]
        result = reconcile(self.pihole, self.config)
        writes = [c for c in self.pihole.calls if c[0].startswith(("add_", "update_", "delete_"))]
        self.assertEqual(writes, [])
        self.assertFalse(result.gravity_required)

    def test_a_list_with_no_domains_loaded_requires_gravity(self):
        self.pihole.lists_[1] = AdList("http://b", ListType.BLOCK, groups=(0,), domain_count=0)
        self.config["block_lists"][0]["groups"] = ["Default"]
        self.assertTrue(reconcile(self.pihole, self.config).gravity_required)

    def test_adding_a_list_sends_group_ids_and_requires_gravity(self):
        self.config["block_lists"].append({"url": "http://c", "groups": ["Default", "Extra"], "comment": "new"})
        result = reconcile(self.pihole, self.config)
        # Group *ids*: posting the names creates a list attached to no group.
        self.assertEqual(self.pihole.of("add_list"), [("http://c", ListType.BLOCK, (0, 7), "new")])
        self.assertTrue(result.gravity_required)

    def test_removing_a_list_requires_gravity(self):
        self.config["block_lists"].pop()
        result = reconcile(self.pihole, self.config)
        self.assertEqual(self.pihole.of("delete_list"), [("http://b", ListType.BLOCK)])
        self.assertTrue(result.gravity_required)


class Groups(Quiet):
    def test_ids_are_read_back_so_a_concurrent_create_is_known_too(self):
        class Raced(FakePihole):
            def add_group(self, name):
                self.groups_[name] = Group(9, name)  # another writer got there first
                raise PiholeError("exists", method="POST", route="/api/groups", status=HTTPStatus.CONFLICT)

        pihole = Raced(groups=GROUPS)
        config = {"groups": [{"name": "Kids"}], "allow_domains": [{"domain": "a.example", "groups": ["Kids"]}]}
        result = reconcile(pihole, config)
        self.assertTrue(result.ok, "a 409 on add is not an error")
        self.assertEqual(pihole.of("add_domain"), [("a.example", DomainType.ALLOW, (9,), "")])

    def test_a_failed_read_ends_the_run_instead_of_reading_as_empty(self):
        # Read as empty, every object's groups would be written back as [].
        pihole = FakePihole(groups=GROUPS, refuse={"groups": HTTPStatus.UNAUTHORIZED})
        with self.assertRaises(PiholeError):
            reconcile(pihole, {"groups": [{"name": "Kids"}]})
        self.assertEqual(pihole.of("update_list") + pihole.of("update_domain") + pihole.of("update_client"), [])

    def test_default_is_never_removed_and_extras_are(self):
        pihole = FakePihole(groups=(*GROUPS, Group(8, "Old")))
        result = reconcile(pihole, {"groups": [{"name": "Extra"}]})
        self.assertEqual(pihole.of("delete_group"), [("Old",)])
        self.assertEqual(result.changes[Change(Object.GROUP, Action.REMOVED)], 1)


class Failures(Quiet):
    def test_a_refused_write_is_an_error_and_the_rest_still_run(self):
        pihole = FakePihole(groups=GROUPS, refuse={"add_domain": HTTPStatus.BAD_REQUEST})
        config = {"block_domains": [{"domain": "a.example"}, {"domain": "b.example"}]}
        result = reconcile(pihole, config)
        self.assertEqual(len(pihole.of("add_domain")), 2, "the second domain must still be tried")
        self.assertEqual(
            [(e.code, e.fatal, e.context["status"]) for e in result.errors], [(ErrorCode.PIHOLE_API, False, 400)] * 2
        )
        self.assertEqual(sum(result.changes.values()), 0)

    def test_pihole_out_of_reach_on_a_write_ends_the_run(self):
        pihole = FakePihole(groups=GROUPS, refuse={"add_domain": None})
        config = {"block_domains": [{"domain": "a.example"}, {"domain": "b.example"}]}
        with self.assertRaises(PiholeError):
            reconcile(pihole, config)
        self.assertEqual(len(pihole.of("add_domain")), 1, "no point trying the rest")

    def test_deny_domains_are_reported_as_block(self):
        result = reconcile(FakePihole(groups=GROUPS), {"block_domains": [{"domain": "x.example"}]})
        self.assertEqual(result.changes[Change(Object.DOMAIN, Action.ADDED, Kind.BLOCK)], 1)

    def test_an_unknown_group_is_skipped_not_fatal(self):
        pihole = FakePihole(groups=GROUPS)
        reconcile(pihole, {"clients": [{"address": "10.0.0.5", "groups": ["Default", "Nope"]}]})
        self.assertEqual(pihole.of("add_client"), [("10.0.0.5", (0,), "")])

    def test_a_client_is_regrouped_with_its_comment_from_the_config(self):
        pihole = FakePihole(groups=GROUPS, clients=[Client("10.0.0.5", groups=(0,), comment="old")])
        reconcile(pihole, {"clients": [{"address": "10.0.0.5", "groups": ["Extra"], "comment": "tv"}]})
        self.assertEqual(pihole.of("update_client"), [(Client("10.0.0.5", groups=(7,), comment="tv"),)])
