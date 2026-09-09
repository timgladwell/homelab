#!/usr/bin/env python3
"""
Landing page server: the static quick-links page, plus the two Pi-hole calls
the user-scope blocking control needs.

This process exists for one reason. Pi-hole has an admin password again
(base/dns/pihole-deployment.yaml), and Pi-hole has no user role — the only
account it has is full admin. So the credential cannot be handed to a browser,
and the browser is offered exactly two operations instead: read the blocking
state, and set it. Everything else Pi-hole's API can do stays behind the admin
login at pihole.<site>, which is the whole point of the split.

Deliberately stdlib only, on the same official python image base/pihole-sync
already pins. The page is expected to be opened about weekly; a framework and
an ASGI server would be two more things to keep patched for a workload that is
idle almost all of the time.

Tested by tests/test_landing.py, which validation step 13 runs.
"""
import json
import os
import sys
import threading
import time
import urllib.error
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
    # urllib.error.URLError propagates — _proxy turns it into a 502.


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
            status, resp = _call(method, path, body)
        except Exception as exc:
            log(f"{method} {path} failed: {exc}", err=True)
            return self._send(502, {"error": "Pi-hole is not responding"})
        if status >= 400:
            # The upstream body can carry detail this page has no business
            # showing an unauthenticated caller; it goes to the log instead.
            log(f"{method} {path} returned {status}", err=True)
            return self._send(502, {"error": "Pi-hole rejected the request"})
        self._send(200, {"blocking": resp.get("blocking"), "timer": resp.get("timer")})

    def do_GET(self):
        if self.path == "/healthz":
            # Deliberately does not touch Pi-hole: a Pi-hole outage should show
            # up on the page as an error, not restart this pod in a loop.
            return self._send(200, {"ok": True})
        if self.path in ("/", "/index.html"):
            try:
                with open(CONTENT_PATH, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError as exc:
                log(f"cannot read {CONTENT_PATH}: {exc}", err=True)
                return self._send(500, {"error": "page unavailable"})
        if self.path == "/api/blocking":
            return self._proxy("GET", "/api/dns/blocking")
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
