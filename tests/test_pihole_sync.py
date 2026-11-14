"""base/pihole-sync/sync.py — the reconciliation logic, with Pi-hole faked out.

Ported from the `--self-check` that used to live at the bottom of sync.py.
That check tested the right thing and nothing ever ran it; the point of moving
it here is that validation step 13 does.
"""

import contextlib
import io
import unittest

import logs
import metrics
import results
import sync


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
        self.fake = FakePihole(
            lists=[
                {"address": "http://a", "id": 1, "number": 5, "groups": [0]},
                {"address": "http://b", "id": 2, "number": 5, "groups": [0]},
            ]
        )
        sync._request_with_retry = self.fake
        self.cfg = {
            "block_lists": [
                {"url": "http://a", "groups": ["Default", "Extra"]},
                {"url": "http://b", "groups": ["Default"]},
            ]
        }
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
        gravity = sync.sync_lists(self.cfg, "", self.groups, "block", "block_lists").gravity_needed
        self.assertFalse(gravity, "a regroup alone must not trigger gravity")

    def test_no_drift_means_no_writes(self):
        self.cfg["block_lists"][0]["groups"] = ["Default"]
        gravity = sync.sync_lists(self.cfg, "", self.groups, "block", "block_lists").gravity_needed
        self.assertEqual(self.fake.of("PUT"), [])
        self.assertEqual(self.fake.of("POST"), [])
        self.assertFalse(gravity)

    def test_empty_list_forces_gravity(self):
        # number == 0 means a previous gravity run did not finish for that list.
        self.fake.lists[1]["number"] = 0
        self.cfg["block_lists"][0]["groups"] = ["Default"]
        gravity = sync.sync_lists(self.cfg, "", self.groups, "block", "block_lists").gravity_needed
        self.assertTrue(gravity, "a list with 0 domains loaded must re-run gravity")

    def test_new_list_is_added_with_the_right_body(self):
        self.cfg["block_lists"].append({"url": "http://c", "groups": ["Default", "Extra"], "comment": "new one"})
        gravity = sync.sync_lists(self.cfg, "", self.groups, "block", "block_lists").gravity_needed

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
        sync.sync_lists(self.cfg, "", self.groups, "block", "block_lists")
        deletes = self.fake.of("DELETE")
        self.assertEqual(len(deletes), 1, f"expected one DELETE, got {deletes}")
        self.assertEqual(deletes[0][1], "/api/lists/http%3A%2F%2Fb?type=block")


class ResolveGroupIds(unittest.TestCase):
    def test_maps_names_to_ids(self):
        self.assertEqual(sync._resolve_group_ids(["Default", "Extra"], {"Default": 0, "Extra": 7}), [0, 7])

    def test_unknown_group_is_skipped_not_fatal(self):
        # A typo in the config should not abort a whole sync; sync.py logs a
        # warning and carries on with the groups it recognised.
        self.assertEqual(sync._resolve_group_ids(["Default", "Nope"], {"Default": 0}), [0])

    def test_empty(self):
        self.assertEqual(sync._resolve_group_ids([], {"Default": 0}), [])


class Headers(unittest.TestCase):
    """Pi-hole attributes calls by User-Agent; the default is "Python-urllib"."""

    def test_names_the_application(self):
        self.assertIn("pihole-sync", sync._headers()["User-Agent"])

    def test_sid_is_only_sent_when_present(self):
        self.assertNotIn("X-FTL-SID", sync._headers())
        self.assertEqual(sync._headers("abc")["X-FTL-SID"], "abc")


class Route(unittest.TestCase):
    """The template a request's metric is labelled with: no name in it, or each
    domain and list URL would be a series of its own."""

    def test_paths_with_a_name_become_a_template(self):
        for path, route in [
            ("/api/groups/Kids", "/api/groups/{name}"),
            ("/api/lists/http%3A%2F%2Fa%2Fb?type=block", "/api/lists/{url}"),
            ("/api/domains/deny/exact/ads.example.com", "/api/domains/deny/exact/{domain}"),
            ("/api/domains/allow/exact/ok.example.com", "/api/domains/allow/exact/{domain}"),
            ("/api/clients/10.0.0.5", "/api/clients/{address}"),
        ]:
            with self.subTest(path=path):
                self.assertEqual(sync._route(path), route)

    def test_paths_without_a_name_stay_as_they_are_minus_the_query(self):
        for path, route in [
            ("/api/auth", "/api/auth"),
            ("/api/groups", "/api/groups"),
            ("/api/lists?type=block", "/api/lists"),
            ("/api/domains/deny/exact", "/api/domains/deny/exact"),
        ]:
            with self.subTest(path=path):
                self.assertEqual(sync._route(path), route)


class RequestStatus(unittest.TestCase):
    """_request tells the observed block how the call ended."""

    class Reply:
        status = 200

        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def setUp(self):
        self.real_urlopen = sync.urllib.request.urlopen

    def tearDown(self):
        sync.urllib.request.urlopen = self.real_urlopen

    def status_of(self, urlopen):
        sync.urllib.request.urlopen = urlopen
        with self.assertLogs("pihole-sync", level="INFO") as logs:
            try:
                sync._request("GET", "/api/groups/Kids")
            except OSError:
                pass
        return logs.records[0].fields["status"]

    def test_an_answer_reports_its_status(self):
        self.assertEqual(self.status_of(lambda req, timeout: self.Reply()), 200)

    def test_an_http_error_reports_its_status_and_is_still_returned(self):
        def urlopen(req, timeout):
            raise sync.urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b'{"error": "no"}'))

        self.assertEqual(self.status_of(urlopen), 404)
        sync.urllib.request.urlopen = urlopen
        with self.assertLogs("pihole-sync", level="INFO"):
            self.assertEqual(sync._request("GET", "/x"), (404, {"error": "no"}))

    def test_no_answer_reports_an_error(self):
        def urlopen(req, timeout):
            raise sync.urllib.error.URLError("refused")

        self.assertEqual(self.status_of(urlopen), "error")


class ProtectedGroups(unittest.TestCase):
    def test_default_group_is_never_removed(self):
        # Deleting Default detaches every list and client from their group.
        self.assertIn("Default", sync._PROTECTED_GROUPS)


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
        self.real_log, sync.log = sync.log, lambda *a, **k: None
        self.real_request = sync._request_with_retry
        logs.logger.disabled = True  # Result.fail logs through the module

    def tearDown(self):
        logs.logger.disabled = False
        sync.log = self.real_log
        sync._request_with_retry = self.real_request

    def test_a_change_is_counted_by_object_kind_and_action(self):
        sync._request_with_retry = FakePihole(lists=[])
        cfg = {"block_lists": [{"url": "http://c", "groups": ["Default"]}]}
        result = sync.sync_lists(cfg, "", {"Default": 0}, "block", "block_lists")
        self.assertEqual(result.changes[("list", "block", "added")], 1)
        self.assertTrue(result.ok)

    def test_deny_domains_are_reported_as_block(self):
        sync._request_with_retry = FakePihole()
        cfg = {"block_domains": [{"domain": "x.example", "groups": ["Default"]}]}
        result = sync.sync_domains(cfg, "", {"Default": 0}, "deny", "block_domains")
        self.assertEqual(result.changes[("domain", "block", "added")], 1)

    def test_a_failed_object_is_an_error_and_the_rest_still_run(self):
        fake = sync._request_with_retry = FailingPost()
        cfg = {
            "block_domains": [
                {"domain": "a.example", "groups": ["Default"]},
                {"domain": "b.example", "groups": ["Default"]},
            ]
        }
        result = sync.sync_domains(cfg, "", {"Default": 0}, "deny", "block_domains")
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


class ObservedRun(unittest.TestCase):
    """main() hands the run's Result to the metrics, however the run ends."""

    def setUp(self):
        self.real_run = sync._run
        self.real_record = metrics.Metrics.record
        logs.logger.disabled = True

    def tearDown(self):
        logs.logger.disabled = False
        sync._run = self.real_run
        metrics.Metrics.record = self.real_record

    def run_main(self, run):
        """Run main() around `run`; return the Result it recorded."""
        sync._run = run
        recorded = []
        metrics.Metrics.record = lambda me, result: recorded.append(result)
        # main() re-raises so the Job's exit status is unchanged.
        with contextlib.suppress(Exception):
            sync.main()
        self.assertEqual(len(recorded), 1, "the metrics must be recorded exactly once")
        return recorded[0]

    def test_a_clean_run_is_ok(self):
        self.assertTrue(self.run_main(lambda result: None).ok)

    def test_an_error_along_the_way_makes_the_run_a_failure(self):
        def run(result):
            result.fail(results.ErrorCode.PIHOLE_API, "adding domain 'x' failed")

        result = self.run_main(run)
        self.assertFalse(result.ok)
        self.assertEqual([e.fatal for e in result.errors], [False])

    def test_a_fatal_error_reports_a_code_from_the_closed_list(self):
        cases = [
            (results.SyncError(results.ErrorCode.AUTH, "no"), "auth"),
            (results.SyncError(results.ErrorCode.GRAVITY, "no"), "gravity"),
            (OSError("connection refused"), "network"),
            (KeyError("groups"), "unknown"),
        ]
        for exc, code in cases:
            with self.subTest(code=code):

                def run(result, exc=exc):
                    raise exc

                (error,) = self.run_main(run).errors
                self.assertEqual((error.code, error.fatal), (code, True))
                self.assertEqual(error.exception, type(exc).__name__)

    def test_what_was_changed_before_a_fatal_error_is_still_reported(self):
        def run(result):
            result.change("domain", "added", "block")
            raise results.SyncError(results.ErrorCode.PIHOLE_API, "GET /api/clients failed")

        result = self.run_main(run)
        self.assertEqual(result.changes[("domain", "block", "added")], 1)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
