#!/usr/bin/env python3
"""
Landing page server: the static quick-links page, plus the Pi-hole calls the
blocking control and the per-device activity panel need.

This process exists for one reason. Pi-hole has an admin password again
(base/dns/pihole-deployment.yaml), and Pi-hole has no user role — the only
account it has is full admin. So the credential cannot be handed to a browser,
and the browser is offered three fixed operations instead: read the blocking
state, set it, and ask what one client has been resolving. Everything else Pi-hole's API can do stays behind the admin login at
pihole.<site>, which is the whole point of the split.

Deliberately stdlib only, on the same official python image base/pihole-sync
already pins. The page is expected to be opened about weekly; a framework and
an ASGI server would be two more things to keep patched for a workload that is
idle almost all of the time.

Tested by tests/test_landing.py, which validation step 13 runs.
"""
import ipaddress
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PIHOLE_URL = os.environ.get("PIHOLE_URL", "").rstrip("/")
PIHOLE_PASSWORD = os.environ.get("PIHOLE_PASSWORD", "")
CONTENT_PATH = os.environ.get("CONTENT_PATH", "/content/index.html")
PORT = int(os.environ.get("PORT", "8080"))

# Upper bound on a disable, exclusive. Anything longer is an administrator
# turning blocking off, not a user unblocking one broken site — and that is a
# decision that should be made in the admin UI, where it is visible.
MAX_MINUTES = 360
UPSTREAM_TIMEOUT = 10
# Nothing this server accepts is larger than a two-field JSON object.
MAX_BODY = 1024
# Named so Pi-hole's own logs attribute these calls to this server rather
# than to "Python-urllib", which every other script would also claim.
_USER_AGENT = "landing"

# The activity panel is a glance, not a query log — pihole.<site> is still the
# place to page through thousands of rows. RECENT_WINDOW is "what is this
# device doing right now"; the two counts beside it cover Pi-hole's whole
# in-memory window, which is 24h.
RECENT_WINDOW = 300
RECENT_MAX = 50
BLOCKED_MAX = 20
# Every /api/queries call is clamped to this. Nothing on the page renders more
# rows than the longest of the two lists, and see _queries for what a zero
# would otherwise mean.
MAX_ROWS = max(RECENT_MAX, BLOCKED_MAX)

# FTL's blocked statuses (enum query_status, src/enums.h). Used only to mark
# rows in the RECENT_WINDOW feed — the blocked *count* comes from Pi-hole's own
# `upstream=blocklist` filter, so this set falling behind a future release
# costs a label on one row, never a number.
BLOCKED_STATUSES = frozenset({
    "GRAVITY", "REGEX", "DENYLIST", "SPECIAL_DOMAIN",
    "GRAVITY_CNAME", "REGEX_CNAME", "DENYLIST_CNAME",
    "EXTERNAL_BLOCKED_IP", "EXTERNAL_BLOCKED_NULL",
    "EXTERNAL_BLOCKED_NXRA", "EXTERNAL_BLOCKED_EDE15",
})

_sid = None
_sid_lock = threading.Lock()


def log(msg, *, err=False):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"[{ts}] {msg}", file=sys.stderr if err else sys.stdout, flush=True)


# ---------------------------------------------------------------------------
# Pi-hole client
# ---------------------------------------------------------------------------

def _pihole(method, path, body=None, sid=None):
    headers = {"Content-Type": "application/json", "User-Agent": _USER_AGENT}
    if sid:
        headers["X-FTL-SID"] = sid
    req = urllib.request.Request(
        f"{PIHOLE_URL}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        exc.read()
        return exc.code, {}
    # urllib.error.URLError propagates — _read classifies it.


def _login():
    status, resp = _pihole("POST", "/api/auth", {"password": PIHOLE_PASSWORD})
    sid = resp.get("session", {}).get("sid")
    if status not in (200, 201) or not sid:
        raise RuntimeError(f"Pi-hole auth failed ({status})")
    log("Authenticated with Pi-hole.")
    return sid


def _call(method, path, body=None):
    """One Pi-hole call, re-authenticating once if the cached session expired.

    Sessions expire on Pi-hole's own schedule (FTLCONF_webserver_session_timeout)
    and this page may go a week between requests, so an expired session is the
    normal case rather than an error worth surfacing.
    """
    global _sid
    for attempt in (1, 2):
        with _sid_lock:
            if _sid is None:
                _sid = _login()
            sid = _sid
        status, resp = _pihole(method, path, body, sid)
        if status == 401 and attempt == 1:
            with _sid_lock:
                # Only clear the session we actually used — another thread may
                # have logged in again while this call was in flight.
                if _sid == sid:
                    _sid = None
            continue
        return status, resp


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------

def validate(payload):
    """Map a request body to (blocking, timer_seconds), or raise ValueError.

    This is the trust boundary: anything reaching Pi-hole passes through here
    first, so the only requests this server can be made to issue are "block"
    and "stop blocking for 1..MAX_MINUTES-1 minutes".
    """
    if not isinstance(payload, dict):
        raise ValueError("body must be a JSON object")
    blocking = payload.get("blocking")
    if blocking is not True and blocking is not False:
        raise ValueError("blocking must be true or false")
    if blocking:
        return True, None
    minutes = payload.get("minutes")
    # bool is a subclass of int, so `True` would otherwise pass as 1 minute.
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise ValueError("minutes must be a whole number")
    if not 1 <= minutes < MAX_MINUTES:
        raise ValueError(f"minutes must be between 1 and {MAX_MINUTES - 1}")
    return False, minutes * 60


class Unavailable(Exception):
    """Why the activity panel has no data, in words meant for the reader.

    Identifying a device from a request header is brittle by construction —
    it depends on Traefik's configuration, on the browser and the resolver
    agreeing about which address this device has, and on Pi-hole answering.
    None of that is worth a failed request and a red error: the panel reports
    an empty result and says which link in that chain gave way.

    The message is rendered with textContent (CLAUDE.md), which is what makes
    it safe to quote a caller-supplied value back into it.
    """


def _excerpt(value, limit=64):
    """A caller-supplied value, short enough to belong in a sentence."""
    value = value.strip()
    return (value[:limit] + "…") if len(value) > limit else value


def _parse_address(raw, source):
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except ValueError:
        raise Unavailable(
            f"{source} value {_excerpt(raw)} is not an IP address") from None


def client_address(headers, query):
    """Return the device this page is being asked about, or raise Unavailable.

    Traefik is the only hop in front of this server, and for a client it does
    not trust it *overwrites* X-Forwarded-For with the peer address it saw
    rather than appending to it. The last element is therefore the one Traefik
    put there, and anything a client sent itself sits in front of it.

    That only works because base/traefik/helmrelease.yaml sets
    `externalTrafficPolicy: Local`. Under the default, kube-proxy SNATs every
    inbound packet and every device on the LAN arrives as the CNI gateway —
    which is what the Traefik access log recorded for every request it served
    before that setting landed.

    `?client=` overrides it outright. This is a LAN page showing LAN DNS to
    whoever can already reach it; checking the tablet from the laptop is the
    point, not a hole.

    This is the trust boundary for /api/activity, and the reason it is an
    address rather than a string: an FTL wildcard, or a second `&param=`
    smuggled into the upstream query, cannot survive ip_address().

    Each way of failing says which one it was. "No results" with no
    explanation is the state someone would spend an afternoon on.
    """
    override = query.get("client")
    if override:
        return _parse_address(override[0], "Query parameter client")
    raw = headers.get("X-Forwarded-For")
    if raw is None:
        raise Unavailable(
            "Request header X-Forwarded-For is missing — nothing identified "
            "this device. Is Traefik in front of this page?")
    # .rsplit on the raw header rather than a full parse: every element but
    # the last is discarded, so there is nothing to parse.
    raw = raw.rsplit(",", 1)[-1]
    if not raw.strip():
        raise Unavailable("Request header X-Forwarded-For is empty")
    return _parse_address(raw, "Request header X-Forwarded-For")


def client_name(queries):
    """The device's own reverse-DNS name, or None if Pi-hole has none.

    Rides along on rows already fetched, so it costs no extra call. Absent for
    a client with no queries at all, and empty when there is no PTR record —
    the page falls back to the address in both cases.

    Attacker-chosen like every other name here: a device picks its own DHCP
    hostname, so this reaches the page through textContent.
    """
    for q in queries:
        name = (q.get("client") or {}).get("name")
        if name:
            return name
    return None


def activity_rows(queries, limit):
    """Trim upstream query rows to the three fields the page renders.

    The raw rows carry the upstream resolver, list ids and EDE text, none of
    which this page has any business showing an unauthenticated caller — the
    same reason _proxy returns two named fields instead of the body it got.
    """
    return [
        {
            "time": q.get("time"),
            "domain": q.get("domain"),
            "blocked": q.get("status") in BLOCKED_STATUSES,
        }
        for q in queries[:limit]
    ]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "landing"
    sys_version = ""

    def _send(self, status, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        # HEAD gets the headers a GET would produce, and no body — including
        # the Content-Length the body would have had, which is the whole point
        # of the method.
        if self.command != "HEAD":
            self.wfile.write(data)

    def _proxy(self, method, path, body=None):
        """Forward one call and return only the two fields the page uses.

        Verified against pihole:2026.07.2 on 2026-09-07:

            GET  /api/dns/blocking -> {"blocking":"enabled","timer":null,"took":7.7e-05}
            POST /api/dns/blocking -> {"blocking":"disabled","timer":60,"took":0.0107}
            PUT  /api/dns/blocking -> 404 not_found

        So: POST is the verb (PUT 404s), `blocking` is the string "enabled" or
        "disabled" rather than a bool, and `timer` is *fractional* seconds
        remaining (59.7, then 56.599999999999952) or null. `took` is dropped
        here along with anything else a future version adds.
        """
        try:
            resp = self._read(method, path, body)
        except Unavailable as exc:
            # A failed write is a failed request: the caller must not be able
            # to mistake "blocking was not turned off" for "it was".
            return self._send(502, {"error": str(exc)})
        self._send(200, {"blocking": resp.get("blocking"), "timer": resp.get("timer")})

    def _read(self, method, path, body=None):
        """One upstream call: the response, or Unavailable saying what failed.

        The two callers disagree about what a failure means, which is why this
        raises rather than answering. _proxy turns it into a 502 because a
        write that did not happen is a failed request; the activity panel
        turns it into an empty panel and a sentence, because the request
        succeeded and there is simply nothing to show.
        """
        try:
            status, resp = _call(method, path, body)
        except Exception as exc:
            log(f"{method} {path} failed: {exc}", err=True)
            # urlopen raises TimeoutError directly on a read timeout and wraps
            # it in URLError on a connect timeout, so unwrap before testing.
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                raise Unavailable(
                    f"Pi-hole did not answer within {UPSTREAM_TIMEOUT}s") from None
            if isinstance(exc, urllib.error.URLError):
                raise Unavailable("Could not reach Pi-hole") from None
            if isinstance(exc, ValueError):
                # json.loads, from _pihole.
                raise Unavailable(
                    "Pi-hole sent a response this page could not read") from None
            raise Unavailable("Pi-hole is not responding") from None
        if status >= 400:
            # The status is safe to show; the body can carry detail this page
            # has no business showing an unauthenticated caller, so it is
            # logged rather than relayed.
            log(f"{method} {path} returned {status}", err=True)
            raise Unavailable(f"Pi-hole refused the request ({status})")
        return resp

    def _queries(self, length, **params):
        """GET /api/queries with one set of filters, bounded.

        FTL reads `length=0` as its 10,000-row maximum rather than as "no
        rows" (`if(length <= 0 || length > API_QUERIES_MAX_ROWS) length =
        API_QUERIES_MAX_ROWS`), so the natural way to ask this endpoint for
        nothing but a count is also the way to make it serialise the whole
        in-memory database through this pod's 96Mi.

        The counts do not need rows anyway — recordsFiltered is counted over
        every matching row regardless of what is returned — so the bound costs
        nothing, and it is here rather than at the three call sites because a
        call site is exactly where the next zero would appear.
        """
        length = min(max(int(length), 1), MAX_ROWS)
        return self._read(
            "GET", "/api/queries?" + urllib.parse.urlencode({"length": length, **params}))

    def _activity(self, query):
        """Answer "what has this device been resolving?" in three calls.

        Read off FTL's own spec and handler (src/api/docs/content/specs/
        queries.yaml and src/api/queries.c) rather than guessed:

            GET /api/queries?client_ip=<ip>&length=1
              -> {"queries":[...],"recordsTotal":N,"recordsFiltered":M,...}

        `upstream=blocklist` is an FTL pseudo-upstream that expands to
        `q.status IN (<blocked statuses>)`, so "blocked" here is Pi-hole's own
        definition rather than a list maintained in this file.

        `recordsFiltered` is counted over every row matching the filters, not
        over the rows returned — FTL paginates in C, after counting — so both
        totals are exact from a length=1 call. `length=0` is *not* the way to
        ask for a count: FTL reads it as its 10,000-row maximum.

        Always 200, always the same shape. Identifying a device from a request
        header is brittle in ways that are nobody's fault and nothing's bug, so
        every way this can come up empty is reported as an empty panel plus
        `reason`, rather than as a failed request.
        """
        empty = {"client": None, "name": None, "window": RECENT_WINDOW,
                 "queries": None, "blocked": None,
                 "recent_blocked": [], "recent": [], "reason": None}
        try:
            ip = client_address(self.headers, query)
        except Unavailable as exc:
            return self._send(200, {**empty, "reason": str(exc)})
        try:
            self._send(200, {**empty, **self._activity_payload(ip), "reason": None})
        except Unavailable as exc:
            self._send(200, {**empty, "client": ip, "reason": str(exc)})

    def _activity_payload(self, ip):
        """The three calls, or Unavailable. Separated so _activity holds the
        empty shape once and this holds no error handling at all."""
        totals = self._queries(1, client_ip=ip)
        blocked = self._queries(BLOCKED_MAX, client_ip=ip, upstream="blocklist")
        now = int(time.time())
        recent = self._queries(
            RECENT_MAX, client_ip=ip, until=now, **{"from": now - RECENT_WINDOW})
        try:
            return {
                "client": ip,
                "name": client_name(totals.get("queries") or []),
                "queries": totals.get("recordsFiltered"),
                "blocked": blocked.get("recordsFiltered"),
                "recent_blocked": activity_rows(blocked.get("queries") or [], BLOCKED_MAX),
                "recent": activity_rows(recent.get("queries") or [], RECENT_MAX),
            }
        except (AttributeError, TypeError) as exc:
            # A row, or a row's client, that is not the shape FTL documents.
            log(f"unreadable /api/queries response: {exc}", err=True)
            raise Unavailable("Pi-hole sent a response this page could not read") from None

    def _drain(self):
        """Consume the body of a request that does not take one.

        The other half of _read_body's problem, and the half that had been
        missed. do_GET never read a body, so `curl -X GET --data x` left those
        bytes in the socket, and HTTP/1.1 keep-alive parsed them as the head of
        the *next* request on that connection — breaking a request that had
        nothing wrong with it.

        A GET body is never legitimate here, so it is read and dropped rather
        than refused: answering 400 without draining would be the identical
        bug. When Content-Length cannot be trusted there is no safe number of
        bytes to drain, so the connection is closed instead.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            return
        if length > MAX_BODY:
            # Too big to drain politely; hang up rather than read it all.
            self.close_connection = True
            return
        if length:
            self.rfile.read(length)

    def do_GET(self):
        self._drain()
        # /api/activity takes a query string, so routing is on the path alone.
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/healthz":
            # Deliberately does not touch Pi-hole: a Pi-hole outage should show
            # up on the page as an error, not restart this pod in a loop.
            return self._send(200, {"ok": True})
        if url.path in ("/", "/index.html"):
            try:
                with open(CONTENT_PATH, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError as exc:
                log(f"cannot read {CONTENT_PATH}: {exc}", err=True)
                return self._send(500, {"error": "page unavailable"})
        if url.path == "/api/blocking":
            return self._proxy("GET", "/api/dns/blocking")
        if url.path == "/api/activity":
            return self._activity(urllib.parse.parse_qs(url.query))
        self._send(404, {"error": "not found"})

    # BaseHTTPRequestHandler answers an unimplemented method with 501 and logs
    # two lines doing it, so `curl -I https://internal.zerpzorp.com/` reported
    # the page as broken when it was serving fine. Routing is identical to GET;
    # _send is what drops the body.
    do_HEAD = do_GET

    def _read_body(self):
        """Return the request body, or answer the request and return None.

        Always consumes exactly Content-Length bytes before replying. This is
        HTTP/1.1 with keep-alive, so a body left unread stays in the socket and
        is parsed as the start of the next request — meaning a rejected request
        breaks the *following* one rather than just itself. Refusing without
        draining is the bug that shape of code always has.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, {"error": "bad request"})
            return None
        if length > MAX_BODY:
            # Too big to want, and too big to drain politely — so say so and
            # hang up rather than reading it to keep the connection usable.
            self.close_connection = True
            self._send(413, {"error": "request too large"})
            return None
        body = self.rfile.read(length) if length else b""
        if not body:
            self._send(400, {"error": "bad request"})
            return None
        return body

    def do_POST(self):
        body = self._read_body()
        if body is None:
            return
        if self.path != "/api/blocking":
            return self._send(404, {"error": "not found"})
        try:
            payload = json.loads(body)
        except ValueError:
            return self._send(400, {"error": "malformed JSON"})
        try:
            blocking, timer = validate(payload)
        except ValueError as exc:
            return self._send(400, {"error": str(exc)})
        self._proxy("POST", "/api/dns/blocking", {"blocking": blocking, "timer": timer})

    def log_message(self, fmt, *args):
        log(f"{self.address_string()} {fmt % args}")


def main():
    for name in ("PIHOLE_URL", "PIHOLE_PASSWORD"):
        if not os.environ.get(name):
            sys.exit(f"{name} is required")
    log(f"Serving {CONTENT_PATH} on :{PORT}, Pi-hole at {PIHOLE_URL}")
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
