"""base/pihole-sync/logs.py — one JSON object per line, filtered by field."""

import json
import unittest

import logs
import results


class JsonLogs(unittest.TestCase):
    def render(self, msg="a message", level=40, **fields):
        record = logs.logger.makeRecord("pihole-sync", level, __file__, 0, msg, (), None, extra={"fields": fields})
        return json.loads(logs.JsonFormatter().format(record))

    def test_a_line_is_one_json_object_with_time_level_and_msg(self):
        line = self.render(status=401)
        self.assertEqual((line["level"], line["msg"], line["status"]), ("error", "a message", 401))
        self.assertRegex(line["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_an_error_object_carries_the_code_the_exception_and_the_message(self):
        line = self.render(**logs.error_field(results.ErrorCode.NETWORK, "connection refused", exception="URLError"))
        self.assertEqual(
            line["error"], {"code": "network", "fatal": False, "message": "connection refused", "exception": "URLError"}
        )

    def test_retry_is_present_only_when_the_action_will_be_retried(self):
        retry = {"iteration": 1, "max_iterations": 6}
        self.assertEqual(logs.error_field("network", "x", retry=retry)["error"]["retry"], retry)
        self.assertNotIn("retry", logs.error_field("network", "x")["error"])

    def test_a_value_that_is_not_json_is_still_written(self):
        # A line that can't be written is a line that can't explain a failure.
        self.assertEqual(self.render(thing=object())["thing"][:8], "<object ")
