"""Talking to Pi-hole: requests, retries, authentication and the gravity rebuild.

Every call to Pi-hole's API goes through `request`, so each one is observed (a log
line with its details, and a latency sample) and none can skip it.
`request_with_retry` adds exponential backoff on network errors and 5xx/429.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

from logs import error_field, log
from metrics import observed_request
from results import ErrorCode, SyncError

PIHOLE_URL = os.environ["PIHOLE_URL"]
PIHOLE_PASSWORD = os.environ.get("PIHOLE_PASSWORD", "")
# Named so Pi-hole's own logs attribute these calls to this syncer rather than
# to "Python-urllib", which every other script would also claim.
_USER_AGENT = "pihole-sync"
_MAX_RETRIES = 5
_RETRY_BASE_DELAY = 2.0  # seconds; doubles on each attempt


def _headers(sid=None):
    headers = {"Content-Type": "application/json", "User-Agent": _USER_AGENT}
    if sid:
        headers["X-FTL-SID"] = sid
    return headers


# Pi-hole paths that carry a name, as the template a metric is labelled with. A
# path itself has a domain name or a list URL in it, which would make one series
# per object; the log line keeps the full path.
_ROUTES = (
    (re.compile(r"^/api/lists/[^/?]+"), "/api/lists/{url}"),
    (re.compile(r"^/api/domains/(allow|deny)/exact/[^/?]+"), r"/api/domains/\1/exact/{domain}"),
    (re.compile(r"^/api/groups/[^/?]+"), "/api/groups/{name}"),
    (re.compile(r"^/api/clients/[^/?]+"), "/api/clients/{address}"),
)


def route(path):
    for pattern, template in _ROUTES:
        if pattern.match(path):
            return pattern.sub(template, path.split("?")[0])
    return path.split("?")[0]


def request(method, path, body=None, sid=None, timeout=60):
    url = f"{PIHOLE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(sid), method=method)
    with observed_request(method, path, route(path)) as observed:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                observed.status = resp.status
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            observed.status = e.code
            with e:  # closes the response; an HTTPError holds the connection open
                raw = e.read().decode(errors="replace")
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"error": raw}
        # urllib.error.URLError propagates — request_with_retry handles it


def _retry(attempt):
    """The `retry` part of an error line: omitted once no attempts are left."""
    if attempt >= _MAX_RETRIES:
        return None
    return {"iteration": attempt + 1, "max_iterations": _MAX_RETRIES + 1}


def request_with_retry(method, path, body=None, sid=None, timeout=60):
    """Call request with exponential backoff on network errors and 5xx/429."""
    last_exc = None
    for attempt in range(_MAX_RETRIES + 1):
        if attempt:
            delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
            log(
                f"Retrying {method} {path} in {delay:.0f}s (attempt {attempt + 1}/{_MAX_RETRIES + 1})...",
                method=method,
                path=path,
            )
            time.sleep(delay)
        try:
            status, resp = request(method, path, body=body, sid=sid, timeout=timeout)
        except urllib.error.URLError as exc:
            log(
                "Request failed: network error",
                warn=True,
                method=method,
                path=path,
                **error_field(ErrorCode.NETWORK, str(exc), exception=type(exc).__name__, retry=_retry(attempt)),
            )
            last_exc = exc
            continue
        if status == 429 or status >= 500:
            message = f"HTTP {status}: {resp}"
            log(
                "Request failed: transient error",
                warn=True,
                method=method,
                path=path,
                status=status,
                **error_field(ErrorCode.PIHOLE_API, message, retry=_retry(attempt)),
            )
            last_exc = SyncError(ErrorCode.PIHOLE_API, message)
            continue
        return status, resp
    if last_exc:
        raise last_exc
    raise SyncError(ErrorCode.PIHOLE_API, f"{method} {path} failed after {_MAX_RETRIES + 1} attempts")


def authenticate():
    log(f"Checking auth requirements at {PIHOLE_URL}...")
    status, resp = request_with_retry("GET", "/api/auth")
    session = resp.get("session", {})
    if session.get("valid") and not session.get("sid"):
        log(f"{session.get('message', 'no password set')} — proceeding without a session.")
        return ""

    log("Authenticating with Pi-hole...")
    status, resp = request_with_retry("POST", "/api/auth", {"password": PIHOLE_PASSWORD})
    if status not in (200, 201):
        raise SyncError(ErrorCode.AUTH, f"Auth failed ({status}): {resp}")
    session = resp.get("session", {})
    if not session.get("valid"):
        raise SyncError(ErrorCode.AUTH, f"Auth returned invalid session: {resp}")
    sid = session.get("sid") or ""
    log(f"Authenticated (sid={'<none>' if not sid else sid[:8] + '...'}, valid={session.get('valid')})")
    return sid


def run_gravity(sid):
    log("--- Running gravity update (may take several minutes) ---")
    t0 = time.monotonic()
    url = f"{PIHOLE_URL}/api/action/gravity"
    req = urllib.request.Request(url, data=b"", headers=_headers(sid), method="POST")
    try:
        # timeout=300: allow up to 5 minutes of silence between streamed lines.
        # Individual list fetches from external sources can be slow.
        with urllib.request.urlopen(req, timeout=300) as resp:
            while True:
                line = resp.readline()
                if not line:
                    break
                log(f"gravity: {line.decode('utf-8', errors='replace').rstrip()}")
        elapsed = time.monotonic() - t0
        log(f"Gravity update complete ({elapsed:.0f}s).")
    except Exception as exc:
        elapsed = time.monotonic() - t0
        raise SyncError(ErrorCode.GRAVITY, f"Gravity update failed after {elapsed:.0f}s: {exc}") from exc
