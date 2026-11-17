# Drive Health (SMART) on a USB-Attached SSD

Installs `host/disk-health/` on a box that boots from an NVMe SSD behind a USB
bridge, so the drive's own health reaches Prometheus as `zerpzorp_disk_*`:
temperatures, wear, spare capacity, traffic, controller busy time, unsafe
shutdowns and error counts. Today that is Akron only. An SD-card box has no
SMART to read; skip it.

It runs on the host, not in the cluster. A systemd timer starts
`disk_health.py` every 30 s inside a sandbox: no network, one readable device,
and only the `CAP_SYS_RAWIO` capability. It writes a file that node-exporter's
textfile collector reads. The alternative, a privileged exporter pod, would
have been root on the node with a network listener; #702 has the comparison.

**Nothing here writes to the drive.** Every `smartctl` command below only reads
(`-H`, `-A`, `-a`). Never add `-t` (starts a self-test) or `-s` (changes drive
settings).

**Edits in git don't reach the box.** The script and units are copied by hand,
so a change to `host/disk-health/` means repeating *Install* on every box that
has it. [State That Is Not In Git](../current-state/host-state.md) lists where
it runs.

## Prerequisites

Each item says how to check it; the sub-steps are for a box where it doesn't
hold yet. Wait for the shell prompt before pasting the next command: a paste
taken by a command that is still running reaches the shell later as garbage
(`^[[200~`).

1. **`python3` 3.13 or newer.** It ships with the OS, so there is nothing to
   install; ruff holds `host/` to 3.13 syntax (`pyproject.toml`).

   ```bash
   python3 --version
   ```

2. **The drive's device name and its USB bridge.** The drive appears as `sd*`
   because the bridge presents it as a SCSI disk. The bridge decides the
   `smartctl -d` type:

   ```bash
   lsblk -d -o NAME,TRAN,MODEL,SERIAL   # the drive: TRAN usb (Akron: sda)
   lsusb                                # the bridge's USB ID
   ```

   | USB ID | Bridge | `-d` type |
   |---|---|---|
   | `0bda:9210` | Realtek RTL9210 (Akron) | `sntrealtek` |
   | `152d:0583` | JMicron JMS583 | `sntjmicron` |
   | `174c:2362` | ASMedia ASM2362 | `sntasmedia` |

   Only the Realtek row has been run here; the others are smartmontools' own
   names for those chips. Use the one type that matches. Each type sends that
   chip's vendor-specific passthrough commands, and a bridge given the wrong
   ones may reject them (Akron's answers `unsupported scsi opcode`) or, on some
   bridges, misbehave; trying every type in turn is not worth that risk. A
   bridge not in the table: look it up in smartmontools' USB device list
   before trying anything.

   Set both for the commands that follow, so neither is typed twice (set them
   again in a new shell):

   ```bash
   DEVICE=sda TYPE=sntrealtek   # Akron's
   ```

3. **`smartmontools` installed.** It provides `smartctl`, which is all this job
   uses:

   ```bash
   sudo smartctl --version | head -1   # "smartctl 7.4 …": installed
   ```

   `sudo` because `smartctl` is in `/usr/sbin`, which a normal user's `PATH`
   doesn't include. If it isn't installed:

   ```bash
   sudo apt update && sudo apt install smartmontools
   ```

   The package also starts `smartd`, its own monitoring daemon. This job
   neither needs it nor conflicts with it: `smartd` only reads, and doesn't
   start self-tests unless its config asks. It adds nothing here on its own,
   though, since its warnings go to syslog and local mail, which nothing in the
   estate reads. Leave it, or turn it off if you'd rather not run it (Akron's
   is off):

   ```bash
   sudo systemctl disable --now smartmontools   # optional: stops smartd
   ```

4. **The bridge passes SMART through with that type:**

   ```bash
   sudo smartctl -d "$TYPE" -H "/dev/$DEVICE"
   ```

   `SMART overall-health self-assessment test result: PASSED` (or `FAILED`)
   means it works. Anything else means this bridge doesn't pass SMART
   through: stop, and record that in host-state.

5. **The sandbox lets `smartctl` reach the drive.** The unit's restrictions,
   with `ReadWritePaths` left out because the directory comes in the next item:

   ```bash
   sudo systemd-run --pty --wait \
     -p PrivateNetwork=yes -p ProtectSystem=strict -p ProtectHome=yes -p PrivateTmp=yes \
     -p NoNewPrivileges=yes -p DevicePolicy=closed -p "DeviceAllow=/dev/$DEVICE r" \
     -p CapabilityBoundingSet=CAP_SYS_RAWIO -p SystemCallFilter=@system-service \
     -p RuntimeMaxSec=20s -p MemoryMax=32M \
     smartctl -d "$TYPE" -H "/dev/$DEVICE"
   ```

   The same `PASSED` line, then `Finished with result: success`.

6. **node-exporter's textfile directory exists.** The unit fails to start
   without it, because `ProtectSystem=strict` can't bind a missing path.
   Kubelet creates it when node-exporter's pod starts
   (`base/collector/node-exporter.yaml`, `DirectoryOrCreate`), but only once
   that manifest has deployed; creating it first lets the job be installed
   before then. `-p` makes this a no-op when it exists, kubelet uses an
   existing directory as it is, and both create it `root:root 0755`:

   ```bash
   sudo mkdir -p /var/lib/node-exporter/textfile
   ls -ld /var/lib/node-exporter/textfile   # drwxr-xr-x root root
   ```

## Install

1. **Copy the job's three files** into one directory on the box, named for the
   units, from a checkout on your machine. They are named one by one because
   running the tests leaves a `__pycache__/` beside them:

   ```bash
   BOX=10.6.1.3   # Akron's
   ssh "$BOX" mkdir -p /tmp/zerpzorp-disk-health
   scp host/disk-health/{disk_health.py,zerpzorp-disk-health.service,zerpzorp-disk-health.timer} \
       "$BOX":/tmp/zerpzorp-disk-health/
   ```

   On the box, the rest of *Install* reads from there:

   ```bash
   cd /tmp/zerpzorp-disk-health && ls   # the three files
   ```

2. **On a reinstall, keep the box's own edits.** A box that isn't Akron has its
   `--device`, `--type` and `DeviceAllow` edited in the installed unit, and
   copying the file over would put Akron's back. Compare first:

   ```bash
   diff /etc/systemd/system/zerpzorp-disk-health.service zerpzorp-disk-health.service
   ```

   No such file: a first install, carry on. Any difference other than the
   change you are shipping: make the same edits to the copy here before the
   next step.

3. **Put them in place:**

   ```bash
   sudo install -D -m 0644 disk_health.py /usr/local/lib/zerpzorp/disk-health/disk_health.py
   sudo install -m 0644 zerpzorp-disk-health.service zerpzorp-disk-health.timer /etc/systemd/system/
   ```

   If the box isn't Akron, edit `--device`, `--type` and `DeviceAllow` in
   `/etc/systemd/system/zerpzorp-disk-health.service` together, and record
   the type in host-state.

4. **Run it once by hand and read what it wrote**, before the timer runs it
   every 30 s:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl start zerpzorp-disk-health.service && echo "run succeeded"   # returns when it finishes
   journalctl -u zerpzorp-disk-health.service -n 5 --no-pager
   ls -l /var/lib/node-exporter/textfile/
   cat /var/lib/node-exporter/textfile/disk_health.prom
   ```

   - `run succeeded`, and the journal ends `Deactivated successfully`. On a
     failure `systemctl start` says so, and the journal holds the script's
     one-line reason. `systemctl status` and `show` aren't used here: until
     the timer refers to the service, systemd unloads it once the run ends
     and its exit code and memory peak go with it (they are read in
     *Post-checks* instead).
   - `disk_health.prom` is `-rw-r--r-- root root`, and every series carries
     the drive's `serial`; `zerpzorp_disk_smartctl_exit_status` is 0 on a
     healthy drive.

5. **Check the values against `smartctl`'s own reading.** It converts the same
   counters for display, so this checks the script's conversions against a
   source other than its own tests:

   ```bash
   sudo smartctl -d "$TYPE" -A "/dev/$DEVICE"
   ```

   - *Data Units Read/Written* × 512,000 = `zerpzorp_disk_read_bytes_total` /
     `written_bytes_total` (smartctl's `[240 TB]` is the same number).
   - *Power On Hours* × 3,600 = `power_on_seconds_total`; *Controller Busy
     Time* (minutes) × 60 = `controller_busy_seconds_total`.
   - *Percentage Used*, *Available Spare* and its threshold as ratios (7% is
     0.07); *Temperature* and each *Temperature Sensor N* as
     `temperature_celsius` and `temperature_sensor_celsius{sensor="N"}`.

   The two readings are moments apart, so the counters may have moved on a
   little; anything else that differs is a bug: stop here.

6. **Start the timer:**

   ```bash
   sudo systemctl enable --now zerpzorp-disk-health.timer
   ```

## Post-checks

On the box, at least three minutes after the timer starts:

```bash
systemctl list-timers zerpzorp-disk-health.timer --no-pager   # LAST and NEXT 30 s apart
systemctl status zerpzorp-disk-health.service --no-pager      # last run: status=0/SUCCESS
systemctl show zerpzorp-disk-health.service -p MemoryPeak -p ExecMainStartTimestamp -p ExecMainExitTimestamp
                                                 # peak well under MemoryMax (32M); run well under 20 s
journalctl -u zerpzorp-disk-health.service --since "-3min" --no-pager | grep -c Finished   # 6, one per 30 s
ls -l --time-style=+%T /var/lib/node-exporter/textfile/   # disk_health.prom from the last 30 s
```

`list-timers` read in the moment a run starts can show NEXT as `-`; read it
again.

In Grafana, once node-exporter reads the directory (within a minute of the
install, or of node-exporter's rollout if that comes later):

- `zerpzorp_disk_temperature_celsius{site="<site>"}` has a value.
- `time() - node_textfile_mtime_seconds{site="<site>", file=~".*disk_health.prom"}` stays under 60. If it climbs, the timer has stopped or every run is failing: `journalctl -u zerpzorp-disk-health.service` says why.
- `node_textfile_scrape_error{site="<site>"}` is 0.

## Removing it

```bash
sudo systemctl disable --now zerpzorp-disk-health.timer
sudo rm /etc/systemd/system/zerpzorp-disk-health.{service,timer}
sudo rm -r /usr/local/lib/zerpzorp/disk-health
sudo rmdir --ignore-fail-on-non-empty /usr/local/lib/zerpzorp   # gone once no estate job is left
sudo rm -f /var/lib/node-exporter/textfile/disk_health.prom
sudo systemctl daemon-reload
sudo systemctl reset-failed zerpzorp-disk-health.service 2>/dev/null; true
systemctl list-units --all 'zerpzorp-disk-health*' --no-pager   # 0 loaded units listed
```

This leaves `smartmontools`, and `smartd` however you left it, as they are;
nothing here depends on either afterwards. Remove the box from host-state.
