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
import http.client
import json
import threading
import unittest
import urllib.error
import urllib.parse
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
        # /api/activity makes three calls in a fixed order; set this to answer
        # them individually. Left None, every call gets self.reply.
        self.replies = None
        self.explode = None

        def fake_call(method, path, body=None):
            self.calls.append((method, path, body))
            if self.explode is not None:
                raise self.explode
            if self.replies:
                return self.replies.pop(0)
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

    def request(self, method, path, body=None, headers=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers={**({"Content-Type": "application/json"} if body else {}),
                     **(headers or {})},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read(), resp.headers.get("Content-Type")
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.read(), exc.headers.get("Content-Type")

    def json_request(self, method, path, body=None, headers=None):
        status, raw, _ = self.request(method, path, body, headers)
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

    def test_a_get_with_a_body_does_not_break_the_next_request(self):
        # The same keep-alive failure _read_body prevents for POST: bytes left
        # in the socket become the head of the next request on that
        # connection, so the request that breaks is the innocent one after it.
        # urlopen opens a fresh connection per call and cannot see this, which
        # is why this one test speaks HTTP directly.
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("GET", "/api/blocking", body=b'{"blocking":true}')
        first = conn.getresponse()
        self.assertEqual(first.status, 200)
        first.read()
        conn.request("GET", "/healthz")
        resp = conn.getresponse()
        self.assertEqual((resp.status, json.loads(resp.read())), (200, {"ok": True}))

    def test_an_oversized_get_body_hangs_up_rather_than_draining(self):
        # Too big to want and too big to drain politely, so the request is
        # answered and the connection dropped — the same trade _read_body
        # makes at 413. Asserted by the socket being unusable afterwards:
        # close_connection closes it without emitting a Connection header.
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("GET", "/healthz", body=b"x" * (app.MAX_BODY + 1))
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        resp.read()
        with self.assertRaises((http.client.HTTPException, OSError)):
            conn.request("GET", "/healthz")
            conn.getresponse()

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


# One Pi-hole /api/queries reply, shaped the way FTL shapes it. `status` is
# what activity_rows classifies on, and the extra fields are the ones this page
# must not relay.
def queries_reply(rows, filtered, name="laptop"):
    return (200, {
        "queries": [
            {"time": t, "domain": d, "status": st,
             "client": {"ip": "10.6.2.51", "name": name},
             "upstream": "10.43.0.53#5335", "list_id": 4,
             "ede": {"code": 15, "text": "blocked by policy"}}
            for t, d, st in rows
        ],
        "recordsTotal": 99999,
        "recordsFiltered": filtered,
        "took": 0.01,
    })


ACTIVITY_REPLIES = [
    queries_reply([(1789503390.0, "example.com", "FORWARDED")], 1234),
    queries_reply([(1789503395.0, "ads.example.com", "GRAVITY")], 88),
    queries_reply([(1789503396.0, "api.example.com", "FORWARDED"),
                   (1789503397.0, "ads.example.com", "GRAVITY")], 2),
]


class DeviceActivity(ServedByRealHandler):
    """GET /api/activity — which device it asks about, and what it asks."""

    LAPTOP = {"X-Forwarded-For": "10.6.2.51"}

    def activity(self, headers=LAPTOP, path="/api/activity"):
        self.replies = list(ACTIVITY_REPLIES)
        return self.json_request("GET", path, headers=headers)

    def test_three_calls_all_scoped_to_one_client(self):
        status, _ = self.activity()
        self.assertEqual(status, 200)
        self.assertEqual(len(self.calls), 3)
        for method, path, body in self.calls:
            with self.subTest(path=path):
                self.assertEqual((method, body), ("GET", None))
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
                self.assertEqual(urllib.parse.urlsplit(path).path, "/api/queries")
                self.assertEqual(query["client_ip"], ["10.6.2.51"])

    def test_counts_come_from_a_length_one_call_not_length_zero(self):
        # length=0 is not "no rows" to FTL, it is its 10,000-row maximum — the
        # one way to ask this endpoint for a count and get a dump instead.
        self.activity()
        first = urllib.parse.parse_qs(urllib.parse.urlsplit(self.calls[0][1]).query)
        self.assertEqual(first["length"], ["1"])
        self.assertNotIn("upstream", first)
        for _, path, _ in self.calls:
            length = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["length"][0]
            self.assertNotEqual(length, "0", f"length=0 in {path}")

    def test_a_zero_length_is_never_sent_upstream(self):
        # FTL reads length=0 as its 10,000-row maximum, not as "no rows", so a
        # constant edited to 0 — the obvious way to ask for a count alone —
        # would dump the whole in-memory database through a 96Mi pod. The clamp
        # in _queries is what stops it; this drives it from a call site.
        self.addCleanup(setattr, app, "BLOCKED_MAX", app.BLOCKED_MAX)
        app.BLOCKED_MAX = 0
        self.activity()
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.calls[1][1]).query)
        self.assertEqual(query["length"], ["1"])

    def test_an_oversized_length_is_capped_at_what_the_page_renders(self):
        self.addCleanup(setattr, app, "RECENT_MAX", app.RECENT_MAX)
        app.RECENT_MAX = 99999
        self.activity()
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.calls[2][1]).query)
        self.assertEqual(query["length"], [str(app.MAX_ROWS)])

    def test_blocked_call_uses_piholes_own_blocklist_filter(self):
        self.activity()
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.calls[1][1]).query)
        self.assertEqual(query["upstream"], ["blocklist"])
        self.assertEqual(query["length"], [str(app.BLOCKED_MAX)])

    def test_recent_call_asks_for_exactly_the_window(self):
        self.activity()
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.calls[2][1]).query)
        self.assertEqual(int(query["until"][0]) - int(query["from"][0]), app.RECENT_WINDOW)
        self.assertEqual(query["length"], [str(app.RECENT_MAX)])
        self.assertNotIn("upstream", query)

    def test_counts_are_records_filtered_not_records_total(self):
        # recordsTotal is every query Pi-hole holds, for every client. Reading
        # it here would report the whole house as this device's traffic.
        _, payload = self.activity()
        self.assertEqual(payload["queries"], 1234)
        self.assertEqual(payload["blocked"], 88)

    def test_rows_carry_only_time_domain_and_blocked(self):
        # The upstream rows include the resolver used and EDE text, neither of
        # which belongs in an unauthenticated response.
        _, payload = self.activity()
        self.assertEqual(payload["recent_blocked"],
                         [{"time": 1789503395.0, "domain": "ads.example.com", "blocked": True}])
        self.assertNotIn("blocked by policy", json.dumps(payload))
        self.assertNotIn("10.43.0.53", json.dumps(payload))

    def test_the_devices_own_name_comes_back_with_its_address(self):
        # Pi-hole's reverse-DNS name for the client, off rows already fetched.
        _, payload = self.activity()
        self.assertEqual((payload["name"], payload["client"]), ("laptop", "10.6.2.51"))

    def test_a_nameless_device_reports_no_name_rather_than_an_empty_one(self):
        # FTL sends "" when there is no PTR record. The page branches on this
        # to fall back to the address, so "" and None must not be conflated.
        for case, first in (
            ("no PTR record", queries_reply([(1.0, "example.com", "CACHE")], 1234, name="")),
            ("no queries at all", queries_reply([], 1234)),
        ):
            with self.subTest(case=case):
                self.replies = [first] + ACTIVITY_REPLIES[1:]
                _, payload = self.json_request("GET", "/api/activity", headers=self.LAPTOP)
                self.assertIsNone(payload["name"])

    def test_no_other_devices_name_or_address_is_returned(self):
        # Rows are filtered to one client upstream, and the client object is
        # dropped on the way out regardless — a name is only ever this
        # device's own, never the household's.
        _, payload = self.activity()
        for row in payload["recent"] + payload["recent_blocked"]:
            self.assertEqual(set(row), {"time", "domain", "blocked"})

    def test_recent_rows_are_marked_blocked_or_not(self):
        _, payload = self.activity()
        self.assertEqual([r["blocked"] for r in payload["recent"]], [False, True])

    def test_last_forwarded_for_element_wins(self):
        # Traefik overwrites the header for an untrusted client, so anything a
        # client sent itself is in front of the address Traefik saw.
        _, payload = self.activity(headers={"X-Forwarded-For": "1.2.3.4, 10.6.2.51"})
        self.assertEqual(payload["client"], "10.6.2.51")
        self.assertIn("client_ip=10.6.2.51", self.calls[0][1])

    def test_query_parameter_overrides_the_header(self):
        _, payload = self.activity(path="/api/activity?client=10.6.2.77")
        self.assertEqual(payload["client"], "10.6.2.77")

    def test_an_empty_override_falls_back_to_the_header(self):
        # parse_qs drops a valueless key, so ?client= is "no override" rather
        # than "look up the empty string".
        _, payload = self.activity(path="/api/activity?client=")
        self.assertEqual(payload["client"], "10.6.2.51")

    def test_ipv6_is_accepted(self):
        _, payload = self.activity(headers={"X-Forwarded-For": "2001:db8::1"})
        self.assertEqual(payload["client"], "2001:db8::1")


class UnresolvableClient(ServedByRealHandler):
    """No device to ask about: an empty panel and a sentence, never an error.

    Identifying a device from a request header is brittle by construction, so
    none of these is a failed request — the request succeeded and there is
    nothing to show. What must hold is that the shape never changes, that the
    reason says which link gave way, and that Pi-hole is never called.
    """

    # The reason text is what a person reads when the panel is blank, so the
    # distinctive part of each is asserted rather than merely "some error".
    BAD = {
        "no header at all":   ("/api/activity", {}, "missing"),
        "empty header":       ("/api/activity", {"X-Forwarded-For": ""}, "empty"),
        "hostname":           ("/api/activity", {"X-Forwarded-For": "laptop.lan"},
                               "laptop.lan is not an IP address"),
        "address with port":  ("/api/activity", {"X-Forwarded-For": "10.6.2.51:443"},
                               "10.6.2.51:443 is not an IP address"),
        "wildcard override":  ("/api/activity?client=10.6.2.%25", {},
                               "Query parameter client value 10.6.2.%"),
        "smuggled parameter": ("/api/activity?client=10.6.2.51%26length%3D9999", {},
                               "Query parameter client"),
        "not an address":     ("/api/activity?client=../../api/auth", {},
                               "not an IP address"),
    }

    EMPTY = {"name": None, "queries": None, "blocked": None,
             "recent_blocked": [], "recent": []}

    def test_an_unusable_client_is_an_empty_panel_not_an_error(self):
        for name, (path, headers, expected) in self.BAD.items():
            with self.subTest(case=name):
                self.calls.clear()
                status, payload = self.json_request("GET", path, headers=headers)
                self.assertEqual(status, 200, f"{name} was answered as an error")
                self.assertEqual(self.calls, [], f"{name} reached Pi-hole")
                self.assertIn(expected, payload["reason"])
                for key, value in self.EMPTY.items():
                    self.assertEqual(payload[key], value, f"{name}: {key}")

    def test_the_rejected_value_is_quoted_back_but_bounded(self):
        # Quoting the value is the point — "no results" with no explanation is
        # what costs an afternoon — and it is safe because the page renders it
        # with textContent (CLAUDE.md). Length is the part worth bounding.
        _, payload = self.json_request("GET", "/api/activity?client=" + "z" * 500)
        self.assertIn("z" * 64, payload["reason"])
        self.assertNotIn("z" * 65, payload["reason"])

    def test_every_failure_keeps_the_same_shape(self):
        # The page branches on `reason` and reads the rest unconditionally, so
        # a missing key here renders as undefined rather than as blank.
        _, good = self.activity_ok()
        _, bad = self.json_request("GET", "/api/activity")
        self.assertEqual(set(good), set(bad))

    def activity_ok(self):
        self.replies = list(ACTIVITY_REPLIES)
        return self.json_request("GET", "/api/activity",
                                 headers={"X-Forwarded-For": "10.6.2.51"})


class UpstreamUnavailable(ServedByRealHandler):
    """Pi-hole failing is also an empty panel, and says how it failed."""

    HEADERS = {"X-Forwarded-For": "10.6.2.51"}

    def reason_for(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        status, payload = self.json_request("GET", "/api/activity", headers=self.HEADERS)
        self.assertEqual(status, 200)
        self.assertEqual(payload["client"], "10.6.2.51", "the device is still named")
        self.assertEqual(payload["recent"], [])
        return payload["reason"]

    def test_a_timeout_says_so_and_names_the_budget(self):
        # urlopen raises TimeoutError directly on a read timeout; a generic
        # "not responding" would hide which of the two it was.
        self.assertIn(str(app.UPSTREAM_TIMEOUT),
                      self.reason_for(explode=TimeoutError("timed out")))

    def test_a_connect_timeout_is_unwrapped_from_urlerror(self):
        self.assertIn(str(app.UPSTREAM_TIMEOUT), self.reason_for(
            explode=urllib.error.URLError(TimeoutError("timed out"))))

    def test_an_unreachable_pihole_is_distinguished_from_a_slow_one(self):
        reason = self.reason_for(explode=urllib.error.URLError("connection refused"))
        self.assertIn("reach", reason)
        self.assertNotIn(str(app.UPSTREAM_TIMEOUT), reason)

    def test_an_unparseable_body_says_the_response_was_unreadable(self):
        self.assertIn("could not read", self.reason_for(explode=ValueError("no json")))

    def test_an_error_status_is_shown_without_the_upstream_body(self):
        reason = self.reason_for(
            reply=(401, {"error": {"key": "unauthorized", "hint": "bad password"}}))
        self.assertIn("401", reason)
        self.assertNotIn("hint", reason)

    def test_a_row_of_the_wrong_shape_does_not_crash_the_panel(self):
        # A future FTL that sends client as a bare string rather than an
        # object would otherwise be an AttributeError and a 500.
        self.replies = [(200, {"queries": ["not a row"], "recordsFiltered": 1})] \
            + ACTIVITY_REPLIES[1:]
        self.assertIn("could not read", self.reason_for())

    def test_a_failed_call_stops_the_rest(self):
        self.reply = (500, {})
        self.json_request("GET", "/api/activity", headers=self.HEADERS)
        self.assertEqual(len(self.calls), 1, "kept calling after a failure")

    def test_the_blocking_control_still_fails_loudly(self):
        # The panel is a display and comes up empty; setting blocking is an
        # action, and an action that did not happen must not look like one
        # that did.
        self.explode = OSError("connection refused")
        status, _ = self.json_request("POST", "/api/blocking",
                                      b'{"blocking":false,"minutes":5}')
        self.assertEqual(status, 502)


class ClientAddressUnit(unittest.TestCase):
    """client_address() on its own, and the status set it is paired with."""

    def test_each_way_of_failing_says_which_one_it_was(self):
        # Three different causes that all look like "no results" on the page.
        for headers, query, expected in (
            ({}, {}, "missing"),
            ({"X-Forwarded-For": "   "}, {}, "empty"),
            ({}, {"client": ["nope"]}, "not an IP address"),
        ):
            with self.subTest(headers=headers, query=query):
                with self.assertRaises(app.Unavailable) as caught:
                    app.client_address(headers, query)
                self.assertIn(expected, str(caught.exception))

    def test_surrounding_whitespace_is_tolerated(self):
        self.assertEqual(
            app.client_address({"X-Forwarded-For": "1.2.3.4,  10.6.2.51 "}, {}),
            "10.6.2.51")

    def test_blocked_statuses_do_not_include_the_permitted_ones(self):
        # A stray FORWARDED or CACHE in the set would mark every ordinary
        # lookup as blocked, which is the failure this list can have.
        for status in ("FORWARDED", "CACHE", "CACHE_STALE", "RETRIED", "UNKNOWN"):
            with self.subTest(status=status):
                self.assertNotIn(status, app.BLOCKED_STATUSES)
        self.assertIn("GRAVITY", app.BLOCKED_STATUSES)
        self.assertIn("DENYLIST_CNAME", app.BLOCKED_STATUSES)


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
