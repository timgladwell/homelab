"""Pi-hole's v6 API, as Python objects.

This is the translation layer. Everything in it is Pi-hole's vocabulary (its
paths, its JSON, its "deny" where the config says "block"), and nothing in it
knows about pihole-config.yaml: it reports what Pi-hole has, as the dataclasses
below, and makes the changes it is asked to. Deciding what to change is
reconcile.py's job.

Every call goes through `PiholeClient._send`, which writes one log line and one
latency sample per attempt, and retries with backoff when Pi-hole is unreachable,
too slow, or answers 429 or 5xx. Any other failure is raised as a `PiholeError`
for the caller to judge, and the caller decides whether the run goes on.
"""

import contextlib
import dataclasses
import enum
import time
import urllib.parse
from collections.abc import Collection, Iterator
from http import HTTPStatus
from typing import Any

import requests
from logs import error_field, log
from metrics import Metrics
from results import ErrorCode

# Named so Pi-hole's own logs attribute these calls to this syncer.
_USER_AGENT = "pihole-sync"
_AUTH = "/api/auth"
_GRAVITY = "/api/action/gravity"


class ListType(enum.StrEnum):
    """Pi-hole's two kinds of adlist."""

    ALLOW = "allow"
    BLOCK = "block"


class DomainType(enum.StrEnum):
    """Pi-hole's two kinds of exact domain. Note "deny", where lists say "block"."""

    ALLOW = "allow"
    DENY = "deny"


@dataclasses.dataclass(frozen=True)
class Group:
    """A Pi-hole group. Other objects refer to it by `id`."""

    id: int
    name: str


@dataclasses.dataclass(frozen=True)
class AdList:
    """An adlist: a URL Pi-hole downloads domains from during a gravity rebuild.

    Attributes:
        domain_count: How many domains the last rebuild loaded from it. 0 means
            a rebuild didn't complete for it; None means Pi-hole didn't say.
    """

    address: str
    type: ListType
    groups: tuple[int, ...] = ()
    enabled: bool = True
    comment: str = ""
    domain_count: int | None = None


@dataclasses.dataclass(frozen=True)
class Domain:
    """An exact domain on Pi-hole's allow or deny list."""

    domain: str
    type: DomainType
    groups: tuple[int, ...] = ()
    enabled: bool = True
    comment: str = ""


@dataclasses.dataclass(frozen=True)
class Client:
    """A client Pi-hole recognises, by address, and the groups it belongs to."""

    address: str
    groups: tuple[int, ...] = ()
    comment: str = ""


def _transient(status: int | None) -> bool:
    """True for a failure worth retrying: no answer, 429, or a 5xx."""
    return status is None or status == HTTPStatus.TOO_MANY_REQUESTS or status >= HTTPStatus.INTERNAL_SERVER_ERROR


class PiholeError(Exception):
    """A call to Pi-hole failed, after any retries.

    Attributes:
        method: The HTTP method of the call.
        route: The path template, such as `/api/groups/{name}`.
        status: The HTTP status Pi-hole answered with, or None when it never
            answered. A response whose shape wasn't what this module expects
            keeps its (successful) status.
    """

    def __init__(self, message: str, *, method: str, route: str, status: int | None):
        """Create the error; the arguments are the attributes, plus the message."""
        super().__init__(message)
        self.method = method
        self.route = route
        self.status = status

    @property
    def transient(self) -> bool:
        """True when it is the kind of failure that was retried: it may pass."""
        return _transient(self.status)

    @property
    def code(self) -> ErrorCode:
        """The `ErrorCode` the failure is reported under."""
        if self.status is None:
            return ErrorCode.NETWORK
        if self.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            return ErrorCode.AUTH
        return ErrorCode.PIHOLE_API


@contextlib.contextmanager
def _parsing(method: str, route: str, status: int) -> Iterator[None]:
    """Turn a response that isn't the shape this module expects into a `PiholeError`.

    A missing key or a wrong type means Pi-hole's API changed, which a retry
    won't fix, so it is reported as such rather than as a crash.
    """
    try:
        yield
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise PiholeError(
            f"{method} {route}: unexpected response shape ({type(exc).__name__}: {exc})",
            method=method,
            route=route,
            status=status,
        ) from exc


class PiholeClient:
    """A logged-in connection to one Pi-hole.

    Use it as a `with` block, which logs in on entry and out on exit, however
    the block ends:

        with PiholeClient(url, password, metrics) as pihole:
            groups = pihole.groups()

    The `requests.Session` underneath keeps the connection open between calls
    and sends the session id on every one.
    """

    def __init__(
        self,
        url: str,
        password: str,
        metrics: Metrics | None = None,
        *,
        attempts: int = 6,
        retry_delay: float = 2.0,
        timeout: tuple[float, float] = (5, 60),
    ):
        """Set up the client; nothing is sent until `login`.

        Args:
            url: Pi-hole's base URL, such as `http://pihole.dns`.
            password: The web password, or empty when Pi-hole has none.
            metrics: Where each request's latency is recorded; None records nothing.
            attempts: How many times a call is tried before it fails.
            retry_delay: Seconds before the first retry; it doubles each time.
            timeout: Seconds to wait to connect, and then for each read.
        """
        self._url = url.rstrip("/")
        self._password = password
        self._metrics = metrics
        self._attempts = attempts
        self._retry_delay = retry_delay
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers["User-Agent"] = _USER_AGENT
        self._logged_in = False

    def __enter__(self) -> "PiholeClient":
        """Log in, and return the client for the `with` block."""
        self.login()
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Log out however the block ended; an exception carries on."""
        self.close()

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def login(self) -> None:
        """Start a session, unless Pi-hole has no password and needs none.

        Raises:
            PiholeError: If Pi-hole refuses the password (code `AUTH`), or can't
                be reached.
        """
        log(f"Checking auth requirements at {self._url}...")
        # 401 is the normal answer here when a password is set.
        status, body = self._send("GET", _AUTH, accept={HTTPStatus.UNAUTHORIZED})
        with _parsing("GET", _AUTH, status):
            session = body.get("session") or {}
            if session.get("valid") and not session.get("sid"):
                log(f"{session.get('message', 'no password set')}: proceeding without a session.")
                return

        log("Logging in to Pi-hole...")
        status, body = self._send("POST", _AUTH, json={"password": self._password})
        with _parsing("POST", _AUTH, status):
            session = body["session"]
            if not session.get("valid") or not session.get("sid"):
                raise PiholeError(
                    f"POST {_AUTH}: Pi-hole returned no valid session: {session.get('message')}",
                    method="POST",
                    route=_AUTH,
                    status=HTTPStatus.UNAUTHORIZED,
                )
            self._session.headers["X-FTL-SID"] = session["sid"]
        self._logged_in = True
        log("Logged in to Pi-hole.")

    def close(self) -> None:
        """Log out, then close the connection. Never raises.

        Pi-hole allows a limited number of sessions at once, and a session not
        logged out holds one until it expires, so a failed logout is a warning:
        enough of them lock this and every other API user out for a while.
        """
        if self._logged_in:
            try:
                self._send("DELETE", _AUTH, attempts=1)
                log("Logged out of Pi-hole.")
            except PiholeError as exc:
                log(
                    "LOGOUT FAILED: the Pi-hole session stays open until it expires, holding one of its limited seats.",
                    warn=True,
                    **error_field(exc.code, str(exc)),
                )
            self._logged_in = False
            self._session.headers.pop("X-FTL-SID", None)
        self._session.close()

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    def groups(self) -> list[Group]:
        """Every group Pi-hole has."""
        status, body = self._send("GET", "/api/groups")
        with _parsing("GET", "/api/groups", status):
            return [Group(id=g["id"], name=g["name"]) for g in body["groups"]]

    def add_group(self, name: str) -> Group:
        """Create a group, enabled, and return it with the id Pi-hole gave it."""
        status, body = self._send("POST", "/api/groups", json={"name": name, "enabled": True})
        with _parsing("POST", "/api/groups", status):
            created = body["groups"][0] if "groups" in body else body["group"]
            return Group(id=created["id"], name=created["name"])

    def delete_group(self, name: str) -> None:
        """Remove a group."""
        self._send("DELETE", "/api/groups/{name}", name=name)

    # ------------------------------------------------------------------
    # Adlists
    # ------------------------------------------------------------------

    def lists(self, list_type: ListType) -> list[AdList]:
        """Every adlist of one type."""
        status, body = self._send("GET", "/api/lists", params={"type": list_type})
        with _parsing("GET", "/api/lists", status):
            return [
                AdList(
                    address=item["address"],
                    type=list_type,
                    groups=tuple(item.get("groups", ())),
                    enabled=item.get("enabled", True),
                    comment=item.get("comment") or "",
                    domain_count=item.get("number"),
                )
                for item in body["lists"]
            ]

    def add_list(self, address: str, list_type: ListType, groups: tuple[int, ...], comment: str) -> None:
        """Add an adlist, enabled. Its domains load at the next gravity rebuild."""
        self._send(
            "POST",
            "/api/lists",
            params={"type": list_type},
            json={"address": address, "enabled": True, "groups": list(groups), "comment": comment},
        )

    def update_list(self, adlist: AdList) -> None:
        """Write an adlist's groups, comment and enabled flag back to Pi-hole."""
        self._send(
            "PUT",
            "/api/lists/{address}",
            address=adlist.address,
            params={"type": adlist.type},
            json={
                "address": adlist.address,
                "enabled": adlist.enabled,
                "comment": adlist.comment,
                "groups": list(adlist.groups),
            },
        )

    def delete_list(self, address: str, list_type: ListType) -> None:
        """Remove an adlist."""
        self._send("DELETE", "/api/lists/{address}", address=address, params={"type": list_type})

    # ------------------------------------------------------------------
    # Exact domains
    # ------------------------------------------------------------------

    def domains(self, domain_type: DomainType) -> list[Domain]:
        """Every exact domain of one type."""
        route = f"/api/domains/{domain_type}/exact"
        status, body = self._send("GET", route)
        with _parsing("GET", route, status):
            return [
                Domain(
                    domain=item["domain"],
                    type=domain_type,
                    groups=tuple(item.get("groups", ())),
                    enabled=item.get("enabled", True),
                    comment=item.get("comment") or "",
                )
                for item in body["domains"]
            ]

    def add_domain(self, domain: str, domain_type: DomainType, groups: tuple[int, ...], comment: str) -> None:
        """Add an exact domain, enabled."""
        self._send(
            "POST",
            f"/api/domains/{domain_type}/exact",
            json={"domain": domain, "enabled": True, "groups": list(groups), "comment": comment},
        )

    def update_domain(self, domain: Domain) -> None:
        """Write a domain's groups, comment and enabled flag back to Pi-hole."""
        self._send(
            "PUT",
            f"/api/domains/{domain.type}/exact/{{domain}}",
            domain=domain.domain,
            json={
                "domain": domain.domain,
                "enabled": domain.enabled,
                "comment": domain.comment,
                "groups": list(domain.groups),
            },
        )

    def delete_domain(self, domain: str, domain_type: DomainType) -> None:
        """Remove an exact domain."""
        self._send("DELETE", f"/api/domains/{domain_type}/exact/{{domain}}", domain=domain)

    # ------------------------------------------------------------------
    # Clients
    # ------------------------------------------------------------------

    def clients(self) -> list[Client]:
        """Every client Pi-hole has a record for."""
        status, body = self._send("GET", "/api/clients")
        with _parsing("GET", "/api/clients", status):
            return [
                Client(address=item["client"], groups=tuple(item.get("groups", ())), comment=item.get("comment") or "")
                for item in body["clients"]
            ]

    def add_client(self, address: str, groups: tuple[int, ...], comment: str) -> None:
        """Add a client by address."""
        self._send("POST", "/api/clients", json={"client": address, "groups": list(groups), "comment": comment})

    def update_client(self, client: Client) -> None:
        """Write a client's groups and comment back to Pi-hole."""
        self._send(
            "PUT",
            "/api/clients/{address}",
            address=client.address,
            json={"client": client.address, "comment": client.comment, "groups": list(client.groups)},
        )

    def delete_client(self, address: str) -> None:
        """Remove a client."""
        self._send("DELETE", "/api/clients/{address}", address=address)

    # ------------------------------------------------------------------
    # Gravity
    # ------------------------------------------------------------------

    def run_gravity(self) -> None:
        """Rebuild Pi-hole's gravity database, logging its progress as it streams.

        It takes minutes, and is never retried here: a retry is a new run, which
        the caller arranges.

        Raises:
            PiholeError: If the rebuild can't start, fails, or goes five minutes
                without a line of output.
        """
        log("--- Running gravity update (may take several minutes) ---")
        started = time.monotonic()
        status = None  # stays None if Pi-hole never answers; the headers set it
        try:
            # The read timeout is per line: a slow list download can be silent for minutes.
            with self._session.post(self._url + _GRAVITY, stream=True, timeout=(self._timeout[0], 300)) as response:
                status = response.status_code
                if not response.ok:
                    raise PiholeError(
                        f"POST {_GRAVITY} answered {status}: {response.text[:500]}",
                        method="POST",
                        route=_GRAVITY,
                        status=status,
                    )
                for line in response.iter_lines():
                    log(f"gravity: {line.decode('utf-8', errors='replace').rstrip()}")
        except requests.RequestException as exc:  # includes going silent part-way through
            raise PiholeError(f"POST {_GRAVITY}: {exc}", method="POST", route=_GRAVITY, status=None) from exc
        finally:
            self._observe("POST", _GRAVITY, _GRAVITY, status, started)
        log(f"Gravity update complete ({time.monotonic() - started:.0f}s).")

    # ------------------------------------------------------------------
    # Every request
    # ------------------------------------------------------------------

    def _send(
        self,
        method: str,
        route: str,
        *,
        params: dict[str, str] | None = None,
        json: Any = None,
        accept: Collection[int] = (),
        attempts: int | None = None,
        **names: str,
    ) -> tuple[int, dict[str, Any]]:
        """Make one call to Pi-hole's API, retrying what may pass.

        Args:
            method: The HTTP method.
            route: The path as a template, such as `/api/groups/{name}`. It is
                the metric label, so a name never becomes a series of its own.
            params: The query string.
            json: The body, sent as JSON.
            accept: Error statuses to return rather than raise.
            attempts: Overrides the client's number of attempts.
            **names: The values for the template's placeholders. Each is
                URL-quoted, since a list's address is itself a URL.

        Returns:
            The status, and the body parsed as JSON (`{}` when there is none).

        Raises:
            PiholeError: When an attempt fails in a way a retry won't fix, or the
                last attempt fails.
        """
        path = route.format(**{k: urllib.parse.quote(v, safe="") for k, v in names.items()})
        shown = f"{path}?{urllib.parse.urlencode(params)}" if params else path
        attempts = attempts or self._attempts
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                time.sleep(self._retry_delay * 2 ** (attempt - 2))
            started = time.monotonic()
            exception = None
            try:
                response = self._session.request(method, self._url + path, params=params, json=json, timeout=self._timeout)
            except requests.RequestException as exc:  # unreachable, refused, or too slow to connect or answer
                status = None
                exception = exc
                error = PiholeError(f"{method} {shown}: {exc}", method=method, route=route, status=None)
            else:
                status = response.status_code
                if response.ok or status in accept:
                    self._observe(method, route, shown, status, started)
                    with _parsing(method, route, status):
                        return status, response.json() if response.content else {}
                error = PiholeError(
                    f"{method} {shown} answered {status}: {response.text[:500]}",
                    method=method,
                    route=route,
                    status=status,
                )
            retrying = error.transient and attempt < attempts
            self._observe(
                method,
                route,
                shown,
                status,
                started,
                error=error if retrying else None,
                exception=exception,
                retry={"iteration": attempt, "max_iterations": attempts} if retrying else None,
            )
            if not retrying:
                raise error from exception
        raise AssertionError("unreachable: the last attempt returns or raises")

    def _observe(
        self,
        method: str,
        route: str,
        path: str,
        status: int | None,
        started: float,
        *,
        error: PiholeError | None = None,
        exception: Exception | None = None,
        retry: dict[str, int] | None = None,
    ) -> None:
        """Write the log line and the latency sample for one attempt.

        The line carries an `error` object only for an attempt that will be
        retried, since nothing else reports it; a final failure is reported by
        whoever handles the `PiholeError`.
        """
        seconds = time.monotonic() - started
        label = "error" if status is None else str(status)
        fields: dict[str, Any] = {"method": method, "path": path, "status": label, "duration_ms": round(seconds * 1000)}
        if error:
            fields.update(
                error_field(error.code, str(error), exception=type(exception).__name__ if exception else None, retry=retry)
            )
        log(f"{method} {path}", warn=status is None or status >= HTTPStatus.BAD_REQUEST, **fields)
        if self._metrics:
            self._metrics.record_request(method, route, label, seconds)
