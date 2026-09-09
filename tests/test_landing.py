"""base/landing/app.py — what the browser can send, and what reaches Pi-hole.

app.py holds Pi-hole's admin password and is reachable unauthenticated by
anything on the LAN, so there are two things worth pinning down:

  * which requests it refuses, and
  * for the ones it accepts, the exact call it makes upstream.

The second is not covered by testing validate() alone. validate() returns
seconds, but do_POST is what chooses the verb, the path and the JSON field
names — a rename or a minutes/seconds mix-up there would pass every
validate() test while sending Pi-hole something it ignores or misreads.

So these tests run the real Handler on a real socket and fake only _call,
which is the seam where app.py stops being ours and starts being Pi-hole's.
"""
import json
import threading
import unittest
import urllib.error
import urllib.request

from loader import REPO_ROOT, load

app = load("base/landing/app.py")


# Incoming request bodies, byte for byte as index.html's send() emits them:
# the three preset buttons, the custom input at both ends of its range, and
# re-enable. Kept as raw bytes rather than dicts so a change to how the page
# serialises a request shows up here.
REQUESTS = {
    "preset 1m":   b'{"blocking":false,"minutes":1}',
    "preset 5m":   b'{"blocking":false,"minutes":5}',
    "preset 30m":  b'{"blocking":false,"minutes":30}',
    "custom max":  b'{"blocking":false,"minutes":359}',
    "re-enable":   b'{"blocking":true}',
}

# What Pi-hole must receive for each. Minutes become seconds here and nowhere
# else, so these numbers are the conversion's only assertion.
EXPECTED_UPSTREAM = {
    "preset 1m":   ("POST", "/api/dns/blocking", {"blocking": False, "timer": 60}),
    "preset 5m":   ("POST", "/api/dns/blocking", {"blocking": False, "timer": 300}),
    "preset 30m":  ("POST", "/api/dns/blocking", {"blocking": False, "timer": 1800}),
    "custom max":  ("POST", "/api/dns/blocking", {"blocking": False, "timer": 21540}),
    "re-enable":   ("POST", "/api/dns/blocking", {"blocking": True, "timer": None}),
}

# Bodies the page should never send and a hand-rolled curl might. Every one of
# these must be refused before app.py touches Pi-hole at all.
BAD_REQUESTS = {
    "empty object":        b'{}',
    "not an object":       b'"blocking"',
    "array":               b'["blocking"]',
    "null":                b'null',
    "malformed json":      b'{"blocking": fals',
    "blocking as string":  b'{"blocking":"false","minutes":5}',
    "blocking as int":     b'{"blocking":0,"minutes":5}',
    "untimed disable":     b'{"blocking":false}',
    "null minutes":        b'{"blocking":false,"minutes":null}',
    "zero minutes":        b'{"blocking":false,"minutes":0}',
    "negative minutes":    b'{"blocking":false,"minutes":-5}',
    "over the maximum":    b'{"blocking":false,"minutes":360}',
    "absurd minutes":      b'{"blocking":false,"minutes":100000}',
    "minutes as string":   b'{"blocking":false,"minutes":"5"}',
    "fractional minutes":  b'{"blocking":false,"minutes":1.5}',
    "minutes as true":     b'{"blocking":false,"minutes":true}',
}


class ServedByRealHandler(unittest.TestCase):
    """Runs app.Handler on a loopback port, with Pi-hole faked at _call."""

    def setUp(self):
        self.calls = []
        self.reply = (200, {"blocking": "enabled", "timer": None})
        self.explode = None

        def fake_call(method, path, body=None):
            self.calls.append((method, path, body))
            if self.explode is not None:
                raise self.explode
            return self.reply

        self._real = (app._call, app.log, app.CONTENT_PATH)
        app._call = fake_call
        app.log = lambda *a, **k: None
        app.CONTENT_PATH = str(REPO_ROOT / "base/landing/index.html")

        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.port = self.server.server_address[1]
        # poll_interval, not a timeout: shutdown() waits up to this long, and
        # the default 0.5s is paid once per test method.
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        app._call, app.log, app.CONTENT_PATH = self._real

    def request(self, method, path, body=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers={"Content-Type": "application/json"} if body else {},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read(), resp.headers.get("Content-Type")
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.read(), exc.headers.get("Content-Type")

    def json_request(self, method, path, body=None):
        status, raw, _ = self.request(method, path, body)
        return status, json.loads(raw)

    def head_content_length(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return int(resp.headers["Content-Length"])


class OutgoingRequestShape(ServedByRealHandler):
    def test_each_button_produces_the_right_pihole_call(self):
        for name, body in REQUESTS.items():
            with self.subTest(request=name):
                self.calls.clear()
                status, _ = self.json_request("POST", "/api/blocking", body)
                self.assertEqual(status, 200)
                self.assertEqual(len(self.calls), 1, "expected exactly one Pi-hole call")
                self.assertEqual(self.calls[0], EXPECTED_UPSTREAM[name])

    def test_timer_is_seconds_not_minutes(self):
        # The one conversion in the whole program, and the one a reader is
        # most likely to "simplify" by passing minutes straight through.
        self.json_request("POST", "/api/blocking", b'{"blocking":false,"minutes":30}')
        self.assertEqual(self.calls[0][2]["timer"], 1800)

    def test_reading_state_is_a_plain_get_with_no_body(self):
        status, _ = self.json_request("GET", "/api/blocking")
        self.assertEqual(status, 200)
        self.assertEqual(self.calls, [("GET", "/api/dns/blocking", None)])

    def test_response_carries_only_blocking_and_timer(self):
        # Pi-hole's reply includes fields the page has no business relaying to
        # an unauthenticated caller (took, session detail, and whatever a
        # future version adds).
        self.reply = (200, {"blocking": "disabled", "timer": 56.599999999999952,
                            "took": 0.01, "secret": "leak me"})
        _, payload = self.json_request("GET", "/api/blocking")
        self.assertEqual(payload, {"blocking": "disabled", "timer": 56.599999999999952})

    def test_fractional_timer_survives_the_round_trip(self):
        # Pi-hole returns seconds remaining as a float, not an int. Coercing it
        # to int here would be a silent behaviour change for the countdown.
        self.reply = (200, {"blocking": "disabled", "timer": 59.7})
        _, payload = self.json_request("GET", "/api/blocking")
        self.assertIsInstance(payload["timer"], float)
        self.assertAlmostEqual(payload["timer"], 59.7)

    def test_blocking_is_a_string_not_a_bool(self):
        # The page branches on the literals "enabled" / "disabled".
        self.reply = (200, {"blocking": "enabled", "timer": None})
        _, payload = self.json_request("GET", "/api/blocking")
        self.assertEqual(payload, {"blocking": "enabled", "timer": None})


class RejectedBeforeUpstream(ServedByRealHandler):
    def test_bad_bodies_are_400_and_never_reach_pihole(self):
        for name, body in BAD_REQUESTS.items():
            with self.subTest(request=name):
                self.calls.clear()
                status, payload = self.json_request("POST", "/api/blocking", body)
                self.assertEqual(status, 400, f"{name} was not rejected")
                self.assertIn("error", payload)
                self.assertEqual(self.calls, [], f"{name} reached Pi-hole")

    def test_oversized_body_is_refused(self):
        big = b'{"blocking":false,"minutes":1,"pad":"' + b'x' * 4096 + b'"}'
        status, _ = self.json_request("POST", "/api/blocking", big)
        self.assertEqual(status, 413)
        self.assertEqual(self.calls, [])

    def test_a_rejected_request_does_not_break_the_next_one(self):
        # HTTP/1.1 keep-alive: a body left unread is parsed as the head of the
        # next request on that connection. This is why _read_body drains before
        # answering, and it is what made this suite take six seconds before.
        self.json_request("POST", "/api/blocking", b'{"blocking":false,"minutes":0}')
        self.json_request("POST", "/nowhere", b'{"blocking":true}')
        self.calls.clear()
        status, _ = self.json_request("POST", "/api/blocking", b'{"blocking":false,"minutes":5}')
        self.assertEqual(status, 200)
        self.assertEqual(self.calls, [("POST", "/api/dns/blocking",
                                       {"blocking": False, "timer": 300})])

    def test_empty_body_is_refused(self):
        status, _ = self.json_request("POST", "/api/blocking", b"")
        self.assertEqual(status, 400)
        self.assertEqual(self.calls, [])


class OtherRoutes(ServedByRealHandler):
    def test_unknown_paths_are_404(self):
        for method, path in (("GET", "/api/stats"), ("GET", "/admin/"),
                             ("POST", "/api/dns/blocking"), ("POST", "/")):
            with self.subTest(path=f"{method} {path}"):
                status, _ = self.json_request(method, path, b'{}' if method == "POST" else None)
                self.assertEqual(status, 404)
                self.assertEqual(self.calls, [])

    def test_healthz_does_not_touch_pihole(self):
        # A Pi-hole outage must not restart this pod: it is the only thing
        # still able to report the outage.
        self.explode = OSError("pihole down")
        status, payload = self.json_request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(self.calls, [])

    def test_head_matches_get_without_a_body(self):
        # `curl -I` is how a person checks a URL, and 501 there reads as an
        # outage. The Content-Length must still be the GET's, not zero.
        get_status, get_raw, get_ctype = self.request("GET", "/")
        status, raw, ctype = self.request("HEAD", "/")
        self.assertEqual((status, ctype), (get_status, get_ctype))
        self.assertEqual(raw, b"")
        self.assertEqual(self.head_content_length("/"), len(get_raw))

    def test_head_on_the_api_does_not_reach_pihole_twice(self):
        # A HEAD is still a real GET upstream; it just returns no body here.
        status, raw, _ = self.request("HEAD", "/api/blocking")
        self.assertEqual((status, raw), (200, b""))
        self.assertEqual(self.calls, [("GET", "/api/dns/blocking", None)])

    def test_root_serves_the_page(self):
        status, raw, ctype = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"<title>", raw)
        # Flux fills these in at reconcile time; the file on disk still has them.
        self.assertIn(b"SITE_DOMAIN", raw)


class UpstreamFailures(ServedByRealHandler):
    def test_unreachable_pihole_is_502_not_500(self):
        self.explode = OSError("connection refused")
        status, payload = self.json_request("GET", "/api/blocking")
        self.assertEqual(status, 502)
        self.assertIn("error", payload)

    def test_pihole_error_status_is_not_relayed_verbatim(self):
        # A 401 upstream means this server's credential is wrong, which is an
        # operator problem — the caller gets a generic 502, and the detail
        # goes to the log.
        self.reply = (401, {"error": {"key": "unauthorized", "hint": "bad password"}})
        status, payload = self.json_request("POST", "/api/blocking",
                                            b'{"blocking":false,"minutes":5}')
        self.assertEqual(status, 502)
        self.assertNotIn("hint", json.dumps(payload))


class UpstreamRequest(unittest.TestCase):
    """The Request _pihole hands to urllib, below the seam the tests above fake."""

    def setUp(self):
        # Loaded with no environment, so PIHOLE_URL is "" and Request() would
        # reject the scheme-less URL before urlopen is reached.
        self._real_url = app.PIHOLE_URL
        app.PIHOLE_URL = "http://pihole.invalid"

    def tearDown(self):
        app.PIHOLE_URL = self._real_url

    def _capture(self, **kwargs):
        sent = []

        class FakeResponse:
            status = 200

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        real = app.urllib.request.urlopen
        app.urllib.request.urlopen = lambda req, timeout=None: (
            sent.append(req), FakeResponse())[1]
        try:
            app._pihole("POST", "/api/dns/blocking", **kwargs)
        finally:
            app.urllib.request.urlopen = real
        return sent[0]

    def test_names_the_application(self):
        # Pi-hole attributes calls by User-Agent; urllib's default is
        # "Python-urllib", which every other script would also send.
        self.assertEqual(self._capture().get_header("User-agent"), "landing")

    def test_sid_is_only_sent_when_present(self):
        self.assertIsNone(self._capture().get_header("X-ftl-sid"))
        self.assertEqual(self._capture(sid="abc").get_header("X-ftl-sid"), "abc")


class ValidateUnit(unittest.TestCase):
    """validate() on its own, for the cases that are awkward to send as JSON."""

    def test_enable_ignores_a_stray_minutes(self):
        self.assertEqual(app.validate({"blocking": True, "minutes": 5}), (True, None))

    def test_bool_is_not_an_integer_minute(self):
        # bool subclasses int, so True passes isinstance(x, int) and would be
        # accepted as 1 minute by a naive range check.
        for value in (True, False):
            with self.subTest(minutes=value):
                with self.assertRaises(ValueError):
                    app.validate({"blocking": False, "minutes": value})

    def test_bounds_are_derived_from_max_minutes(self):
        self.assertEqual(app.MAX_MINUTES, 360)
        self.assertEqual(app.validate({"blocking": False, "minutes": app.MAX_MINUTES - 1}),
                         (False, (app.MAX_MINUTES - 1) * 60))
        with self.assertRaises(ValueError):
            app.validate({"blocking": False, "minutes": app.MAX_MINUTES})


if __name__ == "__main__":
    unittest.main()
