"""base/pihole-sync/sync.py — the reconciliation logic, with Pi-hole faked out.

Ported from the `--self-check` that used to live at the bottom of sync.py.
That check tested the right thing and nothing ever ran it; the point of moving
it here is that validation step 13 does.
"""
import unittest

from loader import load

# PIHOLE_URL is read at import time and has no default.
sync = load("base/pihole-sync/sync.py", env={"PIHOLE_URL": "http://pihole.invalid"})


class FakePihole:
    """Records every call and answers from a canned list of adlists."""

    def __init__(self, lists=None):
        self.calls = []
        self.lists = lists or []

    def __call__(self, method, path, body=None, sid=None, timeout=60):
        self.calls.append((method, path, body))
        if method == "GET" and path.startswith("/api/lists"):
            return 200, {"lists": self.lists}
        return 200, {}

    def of(self, method):
        return [call for call in self.calls if call[0] == method]


class SyncLists(unittest.TestCase):
    """The failure that prompted the original self-check.

    An adlist that already exists but whose groups have drifted from config
    must be PUT back — and that PUT alone must not drag in a gravity rebuild,
    which takes ~10 minutes and was being triggered on every run.
    """

    def setUp(self):
        # sync.py logs a running commentary, which is useful in a Job's pod
        # logs and noise in a test run.
        self.real_log, sync.log = sync.log, lambda *a, **k: None
        self.real = sync._request_with_retry
        # id=1 sits in group 0 only; the config below wants it in 0 and 7.
        self.fake = FakePihole(lists=[
            {"address": "http://a", "id": 1, "number": 5, "groups": [0]},
            {"address": "http://b", "id": 2, "number": 5, "groups": [0]},
        ])
        sync._request_with_retry = self.fake
        self.cfg = {"block_lists": [
            {"url": "http://a", "groups": ["Default", "Extra"]},
            {"url": "http://b", "groups": ["Default"]},
        ]}
        self.groups = {"Default": 0, "Extra": 7}

    def tearDown(self):
        sync._request_with_retry = self.real
        sync.log = self.real_log

    def test_regroups_only_the_drifted_list(self):
        sync.sync_lists(self.cfg, "", self.groups, "block", "block_lists")
        puts = self.fake.of("PUT")
        self.assertEqual(len(puts), 1, f"expected exactly one PUT, got {puts}")
        self.assertEqual(puts[0][1], "/api/lists/http%3A%2F%2Fa?type=block")
        self.assertEqual(sorted(puts[0][2]["groups"]), [0, 7])

    def test_regrouping_does_not_trigger_gravity(self):
        gravity = sync.sync_lists(self.cfg, "", self.groups, "block", "block_lists")
        self.assertFalse(gravity, "a regroup alone must not trigger gravity")

    def test_no_drift_means_no_writes(self):
        self.cfg["block_lists"][0]["groups"] = ["Default"]
        gravity = sync.sync_lists(self.cfg, "", self.groups, "block", "block_lists")
        self.assertEqual(self.fake.of("PUT"), [])
        self.assertEqual(self.fake.of("POST"), [])
        self.assertFalse(gravity)

    def test_empty_list_forces_gravity(self):
        # number == 0 means a previous gravity run did not finish for that list.
        self.fake.lists[1]["number"] = 0
        self.cfg["block_lists"][0]["groups"] = ["Default"]
        gravity = sync.sync_lists(self.cfg, "", self.groups, "block", "block_lists")
        self.assertTrue(gravity, "a list with 0 domains loaded must re-run gravity")

    def test_new_list_is_added_with_the_right_body(self):
        self.cfg["block_lists"].append(
            {"url": "http://c", "groups": ["Default", "Extra"], "comment": "new one"})
        gravity = sync.sync_lists(self.cfg, "", self.groups, "block", "block_lists")

        posts = self.fake.of("POST")
        self.assertEqual(len(posts), 1, f"expected one POST, got {posts}")
        method, path, body = posts[0]
        # type is a query parameter, not a body field — Pi-hole ignores a
        # blocklist POSTed to the allowlist endpoint rather than erroring.
        self.assertEqual(path, "/api/lists?type=block")
        self.assertEqual(body, {
            "address": "http://c",
            "enabled": True,
            # Group *ids*, resolved from names. Posting the names silently
            # creates a list attached to no group.
            "groups": [0, 7],
            "comment": "new one",
        })
        self.assertTrue(gravity, "adding an adlist must re-run gravity")

    def test_removed_list_is_deleted_with_an_escaped_url(self):
        # The URL is a path segment, so an unescaped one truncates at its first
        # slash and deletes whatever that names instead.
        self.cfg["block_lists"] = [{"url": "http://a", "groups": ["Default", "Extra"]}]
        sync.sync_lists(self.cfg, "", self.groups, "block", "block_lists")
        deletes = self.fake.of("DELETE")
        self.assertEqual(len(deletes), 1, f"expected one DELETE, got {deletes}")
        self.assertEqual(deletes[0][1], "/api/lists/http%3A%2F%2Fb?type=block")


class ResolveGroupIds(unittest.TestCase):
    def test_maps_names_to_ids(self):
        self.assertEqual(sync._resolve_group_ids(["Default", "Extra"], {"Default": 0, "Extra": 7}),
                         [0, 7])

    def test_unknown_group_is_skipped_not_fatal(self):
        # A typo in the config should not abort a whole sync; sync.py logs a
        # warning and carries on with the groups it recognised.
        self.assertEqual(sync._resolve_group_ids(["Default", "Nope"], {"Default": 0}), [0])

    def test_empty(self):
        self.assertEqual(sync._resolve_group_ids([], {"Default": 0}), [])


class ProtectedGroups(unittest.TestCase):
    def test_default_group_is_never_removed(self):
        # Deleting Default detaches every list and client from their group.
        self.assertIn("Default", sync._PROTECTED_GROUPS)


if __name__ == "__main__":
    unittest.main()
