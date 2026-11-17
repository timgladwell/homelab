"""Write one NVMe drive's SMART health as Prometheus metrics, for node-exporter.

Runs on the node itself, not in the cluster: a systemd timer starts it every
30 s inside a sandbox (no network, one readable device, one capability; see
zerpzorp-disk-health.service beside this file and docs/runbooks/disk-health.md). It
asks smartctl for the drive's health log as JSON, turns the fields that
change over time into metrics, and writes them where node-exporter's textfile
collector reads them. Why the drive is watched at all: #702.

Standard library only, so it runs on the node's own Python with nothing to
install. On any failure it exits non-zero and leaves the previous file alone.
That way a broken collector shows as a stale file: node-exporter already
reports every file's age as `node_textfile_mtime_seconds`.
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

# An NVMe "data unit" is 1,000 blocks of 512 bytes (NVMe base spec, SMART log).
DATA_UNIT_BYTES = 512_000

# smartctl's exit status is a bit mask. Bit 0 (bad command line) and bit 1
# (device could not be opened) mean there is no data; the higher bits report
# what the drive says about itself (failing, errors logged) and the JSON is
# still complete, so those are written as metrics rather than treated as
# failures.
FATAL_EXIT_BITS = 0b11

# (metric suffix, type, help, health-log key, multiplier). The multiplier
# converts the drive's units into Prometheus base units: seconds, bytes, and
# ratios in 0–1 rather than percentages.
FIELDS = [
    ("temperature_celsius", "gauge", "Composite temperature the drive reports.", "temperature", 1),
    ("available_spare_ratio", "gauge", "Spare capacity left, 1 when new.", "available_spare", 0.01),
    (
        "available_spare_threshold_ratio",
        "gauge",
        "Spare level the drive warns below.",
        "available_spare_threshold",
        0.01,
    ),
    ("endurance_used_ratio", "gauge", "Rated write endurance used; can exceed 1.", "percentage_used", 0.01),
    ("read_bytes_total", "counter", "Bytes the host read from the drive.", "data_units_read", DATA_UNIT_BYTES),
    ("written_bytes_total", "counter", "Bytes the host wrote to the drive.", "data_units_written", DATA_UNIT_BYTES),
    ("read_commands_total", "counter", "Read commands the controller completed.", "host_reads", 1),
    ("write_commands_total", "counter", "Write commands the controller completed.", "host_writes", 1),
    ("controller_busy_seconds_total", "counter", "Time the controller was busy with I/O.", "controller_busy_time", 60),
    ("power_on_seconds_total", "counter", "Time the drive has been powered.", "power_on_hours", 3600),
    ("power_cycles_total", "counter", "Times the drive has been powered on.", "power_cycles", 1),
    ("unsafe_shutdowns_total", "counter", "Power losses without a clean shutdown.", "unsafe_shutdowns", 1),
    ("media_errors_total", "counter", "Unrecovered data integrity errors.", "media_errors", 1),
    ("error_log_entries_total", "counter", "Entries in the drive's error log.", "num_err_log_entries", 1),
    (
        "warning_temperature_seconds_total",
        "counter",
        "Time spent above the warning temperature.",
        "warning_temp_time",
        60,
    ),
    (
        "critical_temperature_seconds_total",
        "counter",
        "Time spent above the critical temperature.",
        "critical_comp_time",
        60,
    ),
    ("critical_warning", "gauge", "The drive's critical-warning bit field; 0 is healthy.", "critical_warning", 1),
]

PREFIX = "zerpzorp_disk_"


class SmartError(Exception):
    """smartctl produced nothing usable: the drive could not be read."""


def run_smartctl(device, device_type):
    """Run smartctl against `device` and return the finished process.

    The script's one call outside itself, kept apart so unit tests replace this
    function rather than the standard library's subprocess.run.
    """
    return subprocess.run(
        ["smartctl", "--json", "-d", device_type, "-a", device],
        capture_output=True,
        text=True,
        check=False,
    )


def read_smart(device, device_type):
    """Run smartctl against `device` and return its parsed JSON report.

    Raises SmartError when the exit status says there is no data, or when the
    report has no NVMe health log (the wrong device type, for instance).
    """
    result = run_smartctl(device, device_type)
    if result.returncode & FATAL_EXIT_BITS:
        raise SmartError(f"smartctl exited {result.returncode}: {smartctl_reason(result)}")
    report = json.loads(result.stdout)
    if "nvme_smart_health_information_log" not in report:
        raise SmartError("smartctl returned no NVMe health log; check the device type")
    return report


def smartctl_reason(result):
    """Why a failed smartctl run had no data, in one line for the journal.

    With --json, smartctl reports its errors inside the JSON on stdout
    (`smartctl.messages`), not on stderr, so that is read first. Captured from
    a missing device on Akron: tests/fixtures/disk_health/smartctl-missing-device.json.
    """
    try:
        messages = json.loads(result.stdout)["smartctl"]["messages"]
        reason = "; ".join(m["string"] for m in messages if m.get("string"))
    except (ValueError, KeyError, TypeError):
        reason = ""
    return reason or result.stderr.strip() or "no message"


def escape(value):
    """Quote a label value for the Prometheus text format."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def number(value):
    """Format a sample value without losing digits.

    Counters reach 15 digits (240 TB read is 2.4e14 bytes), so an integer is
    written whole; only the ratios are fractions, rounded because 57 * 0.01 is
    0.5700000000000001 in floating point.
    """
    return str(value) if isinstance(value, int) else repr(round(value, 6))


def render(report, device):
    """Turn a smartctl JSON report into Prometheus text-format lines.

    Fields the drive doesn't report are left out rather than written as 0,
    so a missing value never reads as a healthy one.

    Every series carries the drive's serial as well as its device name. The
    name is only a slot (`sda` can be a different drive after a replacement, or
    after a second drive changes the order), so the serial is what keeps one
    drive's counters apart from the next one's.
    """
    health = report["nvme_smart_health_information_log"]
    label = f'device="{escape(device)}",serial="{escape(report.get("serial_number", ""))}"'
    lines = []
    for suffix, kind, help_text, key, multiplier in FIELDS:
        if key not in health:
            continue
        name = PREFIX + suffix
        value = number(health[key] * multiplier)
        lines += [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}", f"{name}{{{label}}} {value}"]

    # The drive's own sensors beside the composite above, numbered from 1 as
    # `smartctl -A` prints them ("Temperature Sensor 1"). Which part each one
    # sits on is the drive's choice and isn't reported; telling a hot
    # controller from a hot enclosure is why they are collected (#702).
    sensors = health.get("temperature_sensors", [])
    if sensors:
        name = PREFIX + "temperature_sensor_celsius"
        lines += [f"# HELP {name} Temperature at one of the drive's own sensors.", f"# TYPE {name} gauge"]
        lines += [f'{name}{{{label},sensor="{i}"}} {number(c)}' for i, c in enumerate(sensors, start=1)]

    passed = report.get("smart_status", {}).get("passed")
    if passed is not None:
        name = PREFIX + "smart_passed"
        lines += [
            f"# HELP {name} The drive's overall self-assessment: 1 passed, 0 failed.",
            f"# TYPE {name} gauge",
            f"{name}{{{label}}} {int(passed)}",
        ]

    # Only written when smartctl had data, so bits 0 and 1 are never set here:
    # any non-zero value is smartctl flagging the drive (smartctl(8), EXIT STATUS).
    status = report.get("smartctl", {}).get("exit_status")
    if status is not None:
        name = PREFIX + "smartctl_exit_status"
        lines += [
            f"# HELP {name} smartctl's exit-status bit mask; 0 is clean, see smartctl(8) for each bit.",
            f"# TYPE {name} gauge",
            f"{name}{{{label}}} {status}",
        ]

    name = PREFIX + "info"
    info = ",".join(
        f'{key}="{escape(report.get(field, ""))}"'
        for key, field in [("model", "model_name"), ("firmware", "firmware_version")]
    )
    lines += [f"# HELP {name} The drive's identity; always 1.", f"# TYPE {name} gauge", f"{name}{{{label},{info}}} 1"]
    return "\n".join(lines) + "\n"


def write_atomically(path, text):
    """Replace `path` with `text` in one step, so a reader never sees half a file.

    The temporary file is made in the same directory because os.replace is
    only atomic within one filesystem. node-exporter reads only `*.prom`, so
    the `.tmp` name is never picked up mid-write.
    """
    path = pathlib.Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def main(argv=None):
    """Read the drive, write the metrics file, and return the exit status."""
    args = build_parser().parse_args(argv)
    try:
        report = read_smart(args.device, args.type)
    except (SmartError, json.JSONDecodeError, OSError) as error:
        print(f"disk_health: {error}", file=sys.stderr)
        return 1
    write_atomically(args.output, render(report, pathlib.Path(args.device).name))
    return 0


def build_parser():
    """The command line the systemd unit calls this with."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", required=True, help="block device, e.g. /dev/sda")
    parser.add_argument("--type", required=True, help="smartctl -d type, e.g. sntrealtek for an RTL9210 bridge")
    parser.add_argument("--output", required=True, help="the .prom file node-exporter reads")
    return parser


if __name__ == "__main__":
    sys.exit(main())
