"""host/disk-health/disk_health.py — SMART JSON in, Prometheus text out.

Two kinds of test, for two kinds of risk:

- **Unit tests** (Render, ReadSmart, WriteAtomically, Failures) drive the
  parsing, formatting and error handling from a small hand-built REPORT, so
  each edge case (a missing field, a fatal exit bit) can be set up on its own.
  They replace run_smartctl, the script's one call outside itself.
- **The integration test** (EndToEnd) runs main() against a real report
  captured from Akron's drive, through the real subprocess call, into a real
  file. Only smartctl is replaced, by tests/fixtures/disk_health/smartctl, a
  stand-in executable on PATH that prints the captured report byte for byte. It
  covers the green path only: that is the one case we hold real input for, and
  a failure fed through it would pair a real exit status with a made-up report.
"""

import contextlib
import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from loader import REPO_ROOT, load

disk = load("host/disk-health/disk_health.py")

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "disk_health"

REPORT = {
    "smartctl": {"exit_status": 0},
    "model_name": "SPCC M.2 PCIe SSD",
    "serial_number": "WWDD250304003004331",
    "firmware_version": "APF1M3R1",
    "smart_status": {"passed": True},
    "nvme_smart_health_information_log": {
        "critical_warning": 0,
        "temperature": 38,
        "available_spare": 100,
        "available_spare_threshold": 5,
        "percentage_used": 7,
        "data_units_read": 469541411,
        "data_units_written": 16004607,
        "host_reads": 16562172826,
        "host_writes": 618202032,
        "controller_busy_time": 12883,
        "power_cycles": 123,
        "power_on_hours": 8420,
        "unsafe_shutdowns": 62,
        "media_errors": 0,
        "num_err_log_entries": 0,
        "warning_temp_time": 0,
        "critical_comp_time": 0,
    },
}


def samples(text):
    """The text's sample lines as {metric name: value string}."""
    return {
        line.split("{")[0]: line.rsplit(" ", 1)[1] for line in text.splitlines() if line and not line.startswith("#")
    }


class Render(unittest.TestCase):
    """Units are converted to Prometheus base units, and nothing is rounded away."""

    def setUp(self):
        self.out = samples(disk.render(REPORT, "sda"))

    def test_counters_keep_every_digit(self):
        # 469,541,411 data units of 512,000 bytes: 240 TB, 15 digits.
        self.assertEqual(self.out["zerpzorp_disk_read_bytes_total"], "240405202432000")

    def test_minutes_and_hours_become_seconds(self):
        self.assertEqual(self.out["zerpzorp_disk_controller_busy_seconds_total"], str(12883 * 60))
        self.assertEqual(self.out["zerpzorp_disk_power_on_seconds_total"], str(8420 * 3600))

    def test_percentages_become_ratios(self):
        self.assertEqual(self.out["zerpzorp_disk_endurance_used_ratio"], "0.07")
        self.assertEqual(self.out["zerpzorp_disk_available_spare_ratio"], "1.0")

    def test_health_and_identity(self):
        text = disk.render(REPORT, "sda")
        self.assertEqual(self.out["zerpzorp_disk_smart_passed"], "1")
        self.assertIn('zerpzorp_disk_info{device="sda",serial="WWDD250304003004331",model="SPCC M.2 PCIe SSD"', text)

    def test_every_series_names_the_physical_drive(self):
        # The device name is a slot; the serial tells a replacement drive apart.
        text = disk.render(REPORT, "sda")
        series = [line for line in text.splitlines() if line and not line.startswith("#")]
        self.assertTrue(all('serial="WWDD250304003004331"' in line for line in series))

    def test_drive_flags_are_written_as_smartctls_exit_status(self):
        report = json.loads(json.dumps(REPORT))
        report["smartctl"]["exit_status"] = 0b1000
        self.assertEqual(samples(disk.render(report, "sda"))["zerpzorp_disk_smartctl_exit_status"], "8")

    def test_each_temperature_sensor_is_its_own_series_numbered_from_one(self):
        report = json.loads(json.dumps(REPORT))
        report["nvme_smart_health_information_log"]["temperature_sensors"] = [35, 41]
        lines = disk.render(report, "sda").splitlines()
        self.assertIn(
            'zerpzorp_disk_temperature_sensor_celsius{device="sda",serial="WWDD250304003004331",sensor="1"} 35', lines
        )
        self.assertIn(
            'zerpzorp_disk_temperature_sensor_celsius{device="sda",serial="WWDD250304003004331",sensor="2"} 41', lines
        )

    def test_a_drive_without_sensors_writes_no_sensor_series(self):
        self.assertNotIn("zerpzorp_disk_temperature_sensor_celsius", disk.render(REPORT, "sda"))

    def test_drive_identity_is_escaped_into_the_label(self):
        # Model, serial and firmware come from the drive's firmware: data from
        # outside, so a quote, backslash or newline must not break the line.
        report = json.loads(json.dumps(REPORT))
        report["model_name"] = 'Odd "SSD" \\ v2\nX'
        info = [line for line in disk.render(report, "sda").splitlines() if line.startswith("zerpzorp_disk_info")]
        self.assertEqual(len(info), 1)
        self.assertIn(r'model="Odd \"SSD\" \\ v2\nX"', info[0])

    def test_missing_field_is_left_out_not_zeroed(self):
        report = json.loads(json.dumps(REPORT))
        del report["nvme_smart_health_information_log"]["warning_temp_time"]
        self.assertNotIn("zerpzorp_disk_warning_temperature_seconds_total", samples(disk.render(report, "sda")))


class ReadSmart(unittest.TestCase):
    """Which smartctl results count as no data."""

    def run_with(self, returncode, report=REPORT):
        """read_smart() against a smartctl that exits `returncode` and prints `report`.

        The report's own smartctl.exit_status is set to the same value, as real
        smartctl does, so a test can't pair an exit status with a report that
        contradicts it. autospec makes the stand-in fail if it is called with
        arguments run_smartctl doesn't accept, and the call is checked after.
        """
        report = json.loads(json.dumps(report))
        report.setdefault("smartctl", {})["exit_status"] = returncode
        done = subprocess.CompletedProcess([], returncode, stdout=json.dumps(report), stderr="")
        with mock.patch.object(disk, "run_smartctl", autospec=True, return_value=done) as run:
            try:
                return disk.read_smart("/dev/sda", "sntrealtek")
            finally:
                # Checked even when read_smart raises, as most of these tests expect.
                run.assert_called_once_with("/dev/sda", "sntrealtek")

    def test_drive_reported_problems_return_the_report_unchanged(self):
        # Bit 3: the drive says it is failing. That is a metric, not an error:
        # the report comes back whole, carrying the status through to render().
        report = self.run_with(0b1000)
        self.assertEqual(report["smartctl"]["exit_status"], 8)
        self.assertEqual(report["nvme_smart_health_information_log"], REPORT["nvme_smart_health_information_log"])
        self.assertEqual(samples(disk.render(report, "sda"))["zerpzorp_disk_smartctl_exit_status"], "8")

    def test_device_open_failure_is_an_error(self):
        with self.assertRaises(disk.SmartError):
            self.run_with(0b10)

    def test_no_health_log_is_an_error(self):
        with self.assertRaises(disk.SmartError):
            self.run_with(0, {"smartctl": {"exit_status": 0}})


class WriteAtomically(unittest.TestCase):
    """The file is replaced whole, and no temporary file is left behind."""

    def test_replaces_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "disk_health.prom"
            path.write_text("old\n")
            disk.write_atomically(path, "new\n")
            self.assertEqual(path.read_text(), "new\n")
            self.assertEqual([p.name for p in pathlib.Path(tmp).iterdir()], ["disk_health.prom"])


class Failures(unittest.TestCase):
    """When there is nothing to write: the previous file stays, and stderr says why.

    stderr is all a failing run leaves: systemd puts it in the journal, which
    reaches Loki as unit="zerpzorp-disk-health.service".
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.output = pathlib.Path(tmp.name) / "disk_health.prom"
        self.output.write_text("previous\n")

    def run_main(self, **smartctl):
        """Run main() with run_smartctl replaced; return (exit status, stderr).

        Also checks main() passed the command line's device and type through.
        """
        stderr = io.StringIO()
        args = ["--device", "/dev/sda", "--type", "sntrealtek", "--output", str(self.output)]
        stand_in = mock.patch.object(disk, "run_smartctl", autospec=True, **smartctl)
        with stand_in as run, contextlib.redirect_stderr(stderr):
            status = disk.main(args)
        run.assert_called_once_with("/dev/sda", "sntrealtek")
        return status, stderr.getvalue()

    def assert_failed_saying(self, result, *needles):
        status, stderr = result
        self.assertEqual(status, 1)
        self.assertEqual(self.output.read_text(), "previous\n")
        for needle in needles:
            self.assertIn(needle, stderr)

    def test_without_a_json_reason_falls_back_to_stderr(self):
        # smartctl itself puts its reason in the JSON (MissingDevice below);
        # stderr is the fallback for output that isn't JSON at all.
        done = subprocess.CompletedProcess([], 0b10, stdout="not json", stderr="Smartctl open device: /dev/sda failed")
        self.assert_failed_saying(self.run_main(return_value=done), "smartctl exited 2", "/dev/sda failed")

    def test_wrong_device_type_says_so(self):
        done = subprocess.CompletedProcess([], 0, stdout=json.dumps({"smartctl": {"exit_status": 0}}), stderr="")
        self.assert_failed_saying(self.run_main(return_value=done), "no NVMe health log")

    def test_smartctl_not_installed_says_so(self):
        missing = FileNotFoundError(2, "No such file or directory", "smartctl")
        self.assert_failed_saying(self.run_main(side_effect=missing), "No such file or directory: 'smartctl'")


def run_with_stand_in(report, output, argv_file, device="/dev/sda"):
    """Run main() with the stand-in smartctl first on PATH; return (status, stderr).

    The stand-in prints `report` from the fixtures directory and exits with the
    status smartctl gave with it, read from the report so the two can't disagree.
    The working directory doesn't help find it: an executable is found through
    PATH, and PATH never includes it, so the fixtures directory is added.
    """
    exit_status = json.loads((FIXTURES / report).read_text())["smartctl"]["exit_status"]
    env = {
        "PATH": f"{FIXTURES}{os.pathsep}{os.environ['PATH']}",
        "FAKE_SMARTCTL_REPORT": report,
        "FAKE_SMARTCTL_EXIT": str(exit_status),
        "FAKE_SMARTCTL_ARGV": str(argv_file),
    }
    args = ["--device", device, "--type", "sntrealtek", "--output", str(output)]
    stderr = io.StringIO()
    with mock.patch.dict(os.environ, env), contextlib.redirect_stderr(stderr):
        return disk.main(args), stderr.getvalue()


class MissingDevice(unittest.TestCase):
    """smartctl's real output for a device that isn't there, end to end.

    smartctl-missing-device.json is `smartctl --json -d sntrealtek -a /dev/sdz`
    from Akron on 2026-11-16, unedited, exit status 2. It is also what a drive
    that came back under another name (sdb) would give for /dev/sda.
    """

    def test_keeps_the_previous_file_and_logs_smartctls_reason_on_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "disk_health.prom"
            output.write_text("previous\n")
            status, stderr = run_with_stand_in(
                "smartctl-missing-device.json", output, pathlib.Path(tmp) / "argv", "/dev/sdz"
            )
            self.assertEqual(status, 1)
            self.assertEqual(output.read_text(), "previous\n")
            self.assertEqual(
                stderr,
                "disk_health: smartctl exited 2: Smartctl open device: /dev/sdz [USB NVMe Realtek] failed: No such device\n",
            )


class EndToEnd(unittest.TestCase):
    """A real smartctl report, through the real subprocess call, into a real file.

    smartctl-akron.json is `smartctl --json -d sntrealtek -a /dev/sda` from
    Akron on 2026-11-16 (#702), unedited.
    """

    # TEMPORARY(until: 2027-01-31, issue: #702): disk_health-akron.prom was
    # generated by this script from smartctl-akron.json, with its values checked
    # by hand, so it catches regressions but not a format node-exporter rejects.
    # The PR after #704 replaces it, and smartctl-akron.json, with the first pair
    # Akron produces once installed and node-exporter has read it cleanly.
    EXPECTED = FIXTURES / "disk_health-akron.prom"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = pathlib.Path(tmp.name)
        self.output = self.dir / "disk_health.prom"
        self.argv = self.dir / "smartctl-argv"
        # A restrictive umask, so a 0644 result proves the script sets the mode
        # itself rather than inheriting it.
        self.addCleanup(os.umask, os.umask(0o077))

    def run_main(self):
        """Run main() against Akron's healthy report; return its exit status."""
        status, _ = run_with_stand_in("smartctl-akron.json", self.output, self.argv)
        return status

    def test_writes_the_expected_file_byte_for_byte(self):
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.output.read_bytes(), self.EXPECTED.read_bytes())

    def test_output_is_world_readable_for_node_exporter(self):
        self.run_main()
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o644)

    def test_leaves_only_the_output_file(self):
        self.run_main()
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["disk_health.prom", "smartctl-argv"])

    def test_calls_smartctl_as_the_unit_does(self):
        self.run_main()
        self.assertEqual(self.argv.read_text().split(), ["--json", "-d", "sntrealtek", "-a", "/dev/sda"])


if __name__ == "__main__":
    unittest.main()
