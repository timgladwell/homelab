"""base/pihole-sync/pihole_api.py — talking to Pi-hole: how a request ends, and the route it is labelled with."""

import io
import unittest

import pihole_api


class Headers(unittest.TestCase):
    """Pi-hole attributes calls by User-Agent; the default is "Python-urllib"."""

    def test_names_the_application(self):
        self.assertIn("pihole-sync", pihole_api._headers()["User-Agent"])

    def test_sid_is_only_sent_when_present(self):
        self.assertNotIn("X-FTL-SID", pihole_api._headers())
        self.assertEqual(pihole_api._headers("abc")["X-FTL-SID"], "abc")


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
                self.assertEqual(pihole_api.route(path), route)

    def test_paths_without_a_name_stay_as_they_are_minus_the_query(self):
        for path, route in [
            ("/api/auth", "/api/auth"),
            ("/api/groups", "/api/groups"),
            ("/api/lists?type=block", "/api/lists"),
            ("/api/domains/deny/exact", "/api/domains/deny/exact"),
        ]:
            with self.subTest(path=path):
                self.assertEqual(pihole_api.route(path), route)


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
        self.real_urlopen = pihole_api.urllib.request.urlopen

    def tearDown(self):
        pihole_api.urllib.request.urlopen = self.real_urlopen

    def status_of(self, urlopen):
        pihole_api.urllib.request.urlopen = urlopen
        with self.assertLogs("pihole-sync", level="INFO") as logs:
            try:
                pihole_api.request("GET", "/api/groups/Kids")
            except OSError:
                pass
        return logs.records[0].fields["status"]

    def test_an_answer_reports_its_status(self):
        self.assertEqual(self.status_of(lambda req, timeout: self.Reply()), 200)

    def test_an_http_error_reports_its_status_and_is_still_returned(self):
        def urlopen(req, timeout):
            raise pihole_api.urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b'{"error": "no"}'))

        self.assertEqual(self.status_of(urlopen), 404)
        pihole_api.urllib.request.urlopen = urlopen
        with self.assertLogs("pihole-sync", level="INFO"):
            self.assertEqual(pihole_api.request("GET", "/x"), (404, {"error": "no"}))

    def test_no_answer_reports_an_error(self):
        def urlopen(req, timeout):
            raise pihole_api.urllib.error.URLError("refused")

        self.assertEqual(self.status_of(urlopen), "error")
