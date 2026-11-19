"""base/pihole-sync/pihole_client.py: what is retried, what is raised, and what is labelled.

A fake transport stands in for the network, so the real `requests.Session`
code runs and only the answers are scripted.
"""

import json
import unittest

import requests
from pihole_client import AdList, ListType, PiholeClient, PiholeError
from requests.adapters import BaseAdapter
from results import ErrorCode, classify


class Script(BaseAdapter):
    """Answers each request with the next scripted reply: a status and body, or an exception."""

    def __init__(self, *replies):
        super().__init__()
        self.replies = list(replies)
        self.sent = []

    def send(self, request, **kwargs):
        self.sent.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(body).encode() if body is not None else b""
        response.request = request
        return response

    def close(self):
        pass


class Recorder:
    """Stands in for `metrics.Metrics`, keeping each request sample."""

    def __init__(self):
        self.samples = []

    def record_request(self, method, route, status, seconds):
        self.samples.append((method, route, status))


def client(*replies):
    """A client with no delay between retries, three attempts, and a scripted transport."""
    pihole = PiholeClient("http://pihole.invalid", "pw", Recorder(), attempts=3, retry_delay=0)
    script = Script(*replies)
    pihole._session.mount("http://", script)
    return pihole, script


LOGIN = [(401, {"session": {"valid": False}}), (200, {"session": {"valid": True, "sid": "s1"}})]


class Retries(unittest.TestCase):
    def test_a_read_timeout_is_retried(self):
        pihole, script = client(requests.ReadTimeout("slow"), (200, {"groups": [{"id": 1, "name": "Kids"}]}))
        self.assertEqual([g.name for g in pihole.groups()], ["Kids"])
        self.assertEqual(len(script.sent), 2)

    def test_5xx_is_retried_then_raised_as_transient(self):
        pihole, _ = client((503, {}), (503, {}), (503, {}))
        with self.assertRaises(PiholeError) as caught:
            pihole.groups()
        self.assertTrue(caught.exception.transient)
        self.assertEqual(classify(caught.exception), ErrorCode.PIHOLE_API)

    def test_unreachable_after_every_attempt_is_network(self):
        pihole, _ = client(*[requests.ConnectionError("refused")] * 3)
        with self.assertRaises(PiholeError) as caught:
            pihole.groups()
        self.assertEqual(classify(caught.exception), ErrorCode.NETWORK)
        self.assertIsNone(caught.exception.status)

    def test_a_4xx_is_raised_at_once(self):
        pihole, script = client((409, {"error": "exists"}))
        with self.assertRaises(PiholeError) as caught:
            pihole.add_group("Kids")
        self.assertEqual(caught.exception.status, 409)
        self.assertFalse(caught.exception.transient)
        self.assertEqual(len(script.sent), 1)


class Routes(unittest.TestCase):
    def test_the_metric_carries_the_template_and_the_path_is_quoted(self):
        pihole, script = client((204, None))
        pihole.delete_list("https://a.example/list.txt", ListType.BLOCK)
        self.assertEqual(
            script.sent[0].url, "http://pihole.invalid/api/lists/https%3A%2F%2Fa.example%2Flist.txt?type=block"
        )
        self.assertEqual(pihole._metrics.samples, [("DELETE", "/api/lists/{address}", "204")])

    def test_every_attempt_is_a_sample(self):
        pihole, _ = client(requests.ReadTimeout("slow"), (200, {"clients": []}))
        pihole.clients()
        self.assertEqual(pihole._metrics.samples, [("GET", "/api/clients", "error"), ("GET", "/api/clients", "200")])

    def test_update_writes_the_object_back(self):
        pihole, script = client((200, {}))
        pihole.update_list(AdList("https://a.example/l", ListType.ALLOW, groups=(0, 2), comment="c"))
        self.assertEqual(
            json.loads(script.sent[0].body),
            {"address": "https://a.example/l", "enabled": True, "comment": "c", "groups": [0, 2]},
        )


class Shape(unittest.TestCase):
    def test_an_unexpected_shape_is_a_pihole_error_not_a_crash(self):
        pihole, _ = client((200, {"something_else": []}))
        with self.assertRaises(PiholeError) as caught:
            pihole.groups()
        self.assertEqual(classify(caught.exception), ErrorCode.PIHOLE_API)
        self.assertFalse(caught.exception.transient)


class Session(unittest.TestCase):
    def test_a_refused_password_is_auth_and_not_retried(self):
        pihole, script = client((401, {"session": {"valid": False}}), (401, {"session": {"valid": False}}))
        with self.assertRaises(PiholeError) as caught:
            pihole.login()
        self.assertEqual(classify(caught.exception), ErrorCode.AUTH)
        self.assertEqual(len(script.sent), 2)  # the GET that found a password is needed, and one POST

    def test_no_password_means_no_session_and_no_logout(self):
        pihole, script = client((200, {"session": {"valid": True, "sid": None, "message": "no password set"}}))
        with pihole:
            pass
        self.assertEqual([r.method for r in script.sent], ["GET"])

    def test_the_session_id_is_sent_and_logged_out_even_when_the_block_raises(self):
        pihole, script = client(*LOGIN, (204, None))
        with self.assertRaises(RuntimeError), pihole:
            raise RuntimeError("boom")
        logout = script.sent[-1]
        self.assertEqual((logout.method, logout.headers["X-FTL-SID"]), ("DELETE", "s1"))

    def test_a_failed_logout_warns_and_does_not_raise(self):
        pihole, script = client(*LOGIN, requests.ConnectionError("gone"))
        with self.assertLogs("pihole-sync", "WARNING") as logs, pihole:
            pass
        self.assertIn("LOGOUT FAILED", "\n".join(logs.output))
        self.assertEqual(len(script.sent), 3)  # logout is tried once
