"""base/pihole-sync/reconcile.py — making Pi-hole match the config, with Pi-hole faked out.

Ported from the `--self-check` that used to live at the bottom of sync.py.
That check tested the right thing and nothing ever ran it; the point of moving
it here is that validation step 13 does.
"""

import unittest

import logs
import pihole_api
import reconcile
import results


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
        logs.logger.disabled = True
        self.real = pihole_api.request_with_retry
        # id=1 sits in group 0 only; the config below wants it in 0 and 7.
        self.fake = FakePihole(
            lists=[
                {"address": "http://a", "id": 1, "number": 5, "groups": [0]},
                {"address": "http://b", "id": 2, "number": 5, "groups": [0]},
            ]
        )
        pihole_api.request_with_retry = self.fake
        self.cfg = {
            "block_lists": [
                {"url": "http://a", "groups": ["Default", "Extra"]},
                {"url": "http://b", "groups": ["Default"]},
            ]
        }
        self.groups = {"Default": 0, "Extra": 7}

    def tearDown(self):
        pihole_api.request_with_retry = self.real
        logs.logger.disabled = False

    def test_regroups_only_the_drifted_list(self):
        reconcile.sync_lists(self.cfg, "", self.groups, "block", "block_lists")
        puts = self.fake.of("PUT")
        self.assertEqual(len(puts), 1, f"expected exactly one PUT, got {puts}")
        self.assertEqual(puts[0][1], "/api/lists/http%3A%2F%2Fa?type=block")
        self.assertEqual(sorted(puts[0][2]["groups"]), [0, 7])

    def test_regrouping_does_not_trigger_gravity(self):
        gravity = reconcile.sync_lists(self.cfg, "", self.groups, "block", "block_lists").gravity_needed
        self.assertFalse(gravity, "a regroup alone must not trigger gravity")

    def test_no_drift_means_no_writes(self):
        self.cfg["block_lists"][0]["groups"] = ["Default"]
        gravity = reconcile.sync_lists(self.cfg, "", self.groups, "block", "block_lists").gravity_needed
        self.assertEqual(self.fake.of("PUT"), [])
        self.assertEqual(self.fake.of("POST"), [])
        self.assertFalse(gravity)

    def test_empty_list_forces_gravity(self):
        # number == 0 means a previous gravity run did not finish for that list.
        self.fake.lists[1]["number"] = 0
        self.cfg["block_lists"][0]["groups"] = ["Default"]
        gravity = reconcile.sync_lists(self.cfg, "", self.groups, "block", "block_lists").gravity_needed
        self.assertTrue(gravity, "a list with 0 domains loaded must re-run gravity")

    def test_new_list_is_added_with_the_right_body(self):
        self.cfg["block_lists"].append({"url": "http://c", "groups": ["Default", "Extra"], "comment": "new one"})
        gravity = reconcile.sync_lists(self.cfg, "", self.groups, "block", "block_lists").gravity_needed

        posts = self.fake.of("POST")
        self.assertEqual(len(posts), 1, f"expected one POST, got {posts}")
        _, path, body = posts[0]
        # type is a query parameter, not a body field — Pi-hole ignores a
        # blocklist POSTed to the allowlist endpoint rather than erroring.
        self.assertEqual(path, "/api/lists?type=block")
        self.assertEqual(
            body,
            {
                "address": "http://c",
                "enabled": True,
                # Group *ids*, resolved from names. Posting the names silently
                # creates a list attached to no group.
                "groups": [0, 7],
                "comment": "new one",
            },
        )
        self.assertTrue(gravity, "adding an adlist must re-run gravity")

    def test_removed_list_is_deleted_with_an_escaped_url(self):
        # The URL is a path segment, so an unescaped one truncates at its first
        # slash and deletes whatever that names instead.
        self.cfg["block_lists"] = [{"url": "http://a", "groups": ["Default", "Extra"]}]
        reconcile.sync_lists(self.cfg, "", self.groups, "block", "block_lists")
        deletes = self.fake.of("DELETE")
        self.assertEqual(len(deletes), 1, f"expected one DELETE, got {deletes}")
        self.assertEqual(deletes[0][1], "/api/lists/http%3A%2F%2Fb?type=block")


class ResolveGroupIds(unittest.TestCase):
    def test_maps_names_to_ids(self):
        self.assertEqual(reconcile._resolve_group_ids(["Default", "Extra"], {"Default": 0, "Extra": 7}), [0, 7])

    def test_unknown_group_is_skipped_not_fatal(self):
        # A typo in the config should not abort a whole sync; sync.py logs a
        # warning and carries on with the groups it recognised.
        self.assertEqual(reconcile._resolve_group_ids(["Default", "Nope"], {"Default": 0}), [0])

    def test_empty(self):
        self.assertEqual(reconcile._resolve_group_ids([], {"Default": 0}), [])


class ProtectedGroups(unittest.TestCase):
    def test_default_group_is_never_removed(self):
        # Deleting Default detaches every list and client from their group.
        self.assertIn("Default", reconcile._PROTECTED_GROUPS)


class FailingPost(FakePihole):
    """Answers every POST with an error, and everything else normally."""

    def __call__(self, method, path, body=None, sid=None, timeout=60):
        if method == "POST":
            self.calls.append((method, path, body))
            return 400, {"error": {"message": "nope"}}
        return super().__call__(method, path, body=body, sid=sid, timeout=timeout)


class RunResult(unittest.TestCase):
    """What a sync returns: what it changed, and everything that went wrong.

    Outcomes are values returned by the sync functions, not state kept beside
    them, so these tests assert on what they return.
    """

    def setUp(self):
        logs.logger.disabled = True
        self.real_request = pihole_api.request_with_retry

    def tearDown(self):
        logs.logger.disabled = False
        pihole_api.request_with_retry = self.real_request

    def test_a_change_is_counted_by_object_kind_and_action(self):
        pihole_api.request_with_retry = FakePihole(lists=[])
        cfg = {"block_lists": [{"url": "http://c", "groups": ["Default"]}]}
        result = reconcile.sync_lists(cfg, "", {"Default": 0}, "block", "block_lists")
        self.assertEqual(result.changes[("list", "block", "added")], 1)
        self.assertTrue(result.ok)

    def test_deny_domains_are_reported_as_block(self):
        pihole_api.request_with_retry = FakePihole()
        cfg = {"block_domains": [{"domain": "x.example", "groups": ["Default"]}]}
        result = reconcile.sync_domains(cfg, "", {"Default": 0}, "deny", "block_domains")
        self.assertEqual(result.changes[("domain", "block", "added")], 1)

    def test_a_failed_object_is_an_error_and_the_rest_still_run(self):
        fake = pihole_api.request_with_retry = FailingPost()
        cfg = {
            "block_domains": [
                {"domain": "a.example", "groups": ["Default"]},
                {"domain": "b.example", "groups": ["Default"]},
            ]
        }
        result = reconcile.sync_domains(cfg, "", {"Default": 0}, "deny", "block_domains")
        self.assertFalse(result.ok)
        self.assertEqual([e.code for e in result.errors], [results.ErrorCode.PIHOLE_API] * 2)
        self.assertEqual(len(fake.of("POST")), 2, "the second domain must still be tried")
        self.assertEqual(result.errors[0].context["status"], 400)
        self.assertFalse(result.errors[0].fatal)
        self.assertEqual(sum(result.changes.values()), 0)

    def test_results_merge(self):
        a, b = results.Result(), results.Result()
        a.change("domain", "added", "block")
        b.change("domain", "added", "block")
        b.fail(results.ErrorCode.PIHOLE_API, "boom")
        b.gravity_needed = True
        a.merge(b)
        self.assertEqual(a.changes[("domain", "block", "added")], 2)
        self.assertEqual(len(a.errors), 1)
        self.assertTrue(a.gravity_needed)
