# TRIM on a USB-Attached SSD

## Background

For an SSD behind a USB bridge, such as Akron's NVMe drive, the bridge is the
problem: **TRIM is not enabled by default over USB, and nothing tells you.**

The kernel talks to a USB disk over SCSI, and issues `UNMAP` only when the
disk's `provisioning_mode` is `unmap`. Bridges either do not report the Logical
Block Provisioning VPD page at all or report it in a way the `uas`/`usb-storage`
path does not act on, so Linux leaves the mode at `full` or `disabled`. The
drive underneath supports TRIM perfectly well; the translation layer in front of
it never asks.

The failure is silent in the way this repo keeps being bitten by. `fstrim.timer`
can be enabled and running and reclaiming nothing. The drive does not report an
error, `df` looks fine, and the only symptom is the SSD's own controller slowly
treating every erased block as still in use — write amplification climbs and
sustained write throughput degrades, months later, on a drive that looks idle.

## Where this applies

Any box that boots from an SSD behind a USB bridge — today Akron alone, per
[State That Is Not In Git](../current-state/host-state.md). Akron's diagnosis,
and the write-rate measurements that justified enabling it, are in #351.

**Whether this is fixable depends on the bridge**, so the diagnosis below
cannot be skipped. Some bridges accept `UNMAP` and discard the wrong ranges —
the reason the kernel is conservative here in the first place — so "enable it
and see" is not a safe move on a drive holding anything.

**Why the kernel leaves it off even on a bridge that supports it:** it gates
discard on `LBPME` from `READ CAPACITY(16)` ("is this device thinly
provisioned"), not on `LBPU` ("UNMAP supported"). A bridge that reports itself
fully provisioned therefore gets `provisioning_mode=full` despite advertising
`UNMAP`. Overriding that acts on a capability the device advertised; it does not
force one the device denied. If `LBPWS` and `LBPWS10` are both 0, `unmap` is the
only mode that works — do not substitute `writesame_16`.

**`fstrim.timer` hides the failure.** Debian's unit runs `fstrim` with
`--quiet-unsupported`, which turns "the discard operation is not supported" into
a silent exit 0. A green timer is not evidence that anything is trimmed.

## Diagnose

All of this is read-only. Run it on the box.

1. **Is the drive on `uas`, not `usb-storage`?**
   ```bash
   lsusb -t
   ```
   Look for `Driver=uas` on the disk's interface. `Driver=usb-storage` means
   either the bridge is on the kernel's quirk list or something in
   `/boot/firmware/cmdline.txt` set `usb-storage.quirks=<vid>:<pid>:u`. That
   path does not do `UNMAP`, so **TRIM is unavailable and the rest of this page
   does not apply.** Do not remove a quirk to get TRIM — quirks are there
   because the bridge corrupted data under UAS.

2. **Identify the bridge**, which is what decides whether enabling is safe:
   ```bash
   lsusb          # note the ID <idVendor>:<idProduct> of the disk enclosure
   lsblk -o NAME,MODEL,TRAN,MOUNTPOINTS
   ```

3. **Confirm discard is currently off:**
   ```bash
   lsblk -D
   sudo fstrim -v /
   ```
   `DISC-GRAN` and `DISC-MAX` of `0B` is the tell. `fstrim` failing with *the
   discard operation is not supported* confirms it.

4. **Read the current mode** (glob because the `scsi_disk` H:C:T:L path differs
   per box, and `sda` may not be the right disk — use the name from step 2):
   ```bash
   cat /sys/block/sda/device/scsi_disk/*/provisioning_mode
   ```
   Expect `full` or `disabled`.

5. **Ask the drive whether it can do it at all:**
   ```bash
   sudo apt install -y sg3-utils
   sudo sg_vpd -p lbpv /dev/sda      # Logical block provisioning VPD page
   sudo sg_readcap -l /dev/sda | grep -i 'lbpme\|lbprz'
   ```
   `Unmap command supported (LBPU): 1` means the bridge does advertise it, and
   the kernel simply is not using it — the good case. If the page comes back
   `Invalid field in cdb` or absent, the bridge does not advertise support.

**If the bridge does not advertise `UNMAP`, stop.** Forcing `provisioning_mode`
on a bridge that did not claim support is exactly the case that has been
reported to corrupt filesystems. Record the finding in the PR and leave TRIM
off; the drive will survive without it, and replacing the enclosure with one
that supports it is the real fix.

## Enable

Only with step 5 above confirming support.

1. **Try it live first** — this survives nothing, which is the point:
   ```bash
   echo unmap | sudo tee /sys/block/sda/device/scsi_disk/*/provisioning_mode
   lsblk -D                          # DISC-GRAN / DISC-MAX now non-zero
   sudo fstrim -v /                  # reports bytes trimmed
   ```

2. **Verify nothing was eaten** before making it permanent. `fstrim` only
   discards blocks the filesystem says are free, so a bridge that maps ranges
   wrongly shows up as corruption in live data:
   ```bash
   sha256sum /usr/lib/aarch64-linux-gnu/libc.so.6 | tee /tmp/trim-canary
   sudo fstrim -v /
   sha256sum -c /tmp/trim-canary
   sudo dmesg -T | grep -iE 'I/O error|ext4|unmap' | tail
   ```
   Any mismatch, or ext4 errors in `dmesg`, means **reboot immediately and do
   not persist the change.** A clean pass is evidence but not proof; leave the
   live setting alone for a few days before step 3 if the drive holds anything
   you would miss.

3. **Persist it with a udev rule**, keyed on the bridge's USB IDs from step 2
   (`lsusb` prints them as `<idVendor>:<idProduct>`), and record them in
   [State That Is Not In Git](../current-state/host-state.md).
   The setting is per-device and is lost on reboot or replug without this:
   ```bash
   sudo tee /etc/udev/rules.d/10-usb-ssd-trim.rules <<'RULE'
   # Enable TRIM on the USB-attached SSD. The kernel leaves provisioning_mode
   # at "full" for USB bridges, so discard is a no-op without this — see
   # docs/runbooks/usb-trim.md. IDs are the USB bridge's, not the SSD's
   # behind it — replacing the enclosure means updating them.
   ACTION=="add|change", SUBSYSTEM=="scsi_disk", \
     ATTRS{idVendor}=="<idVendor>", ATTRS{idProduct}=="<idProduct>", \
     ATTR{provisioning_mode}="unmap"
   RULE
   sudo udevadm control --reload-rules
   sudo udevadm trigger --subsystem-match=scsi_disk
   ```
   `ATTRS{}` walks up to the parent USB device; `ATTR{}` is the `scsi_disk`
   node itself. The two spellings are not interchangeable and the rule silently
   matches nothing if they are swapped.

   **Test the rule rather than the value.** Reading back `unmap` proves nothing
   if it was already `unmap` from step 1 — the trigger leaves it that way
   whether or not the rule matched. Set it back and let udev undo that:
   ```bash
   echo full | sudo tee /sys/block/sda/device/scsi_disk/*/provisioning_mode
   sudo udevadm trigger --subsystem-match=scsi_disk
   sudo udevadm settle
   cat /sys/block/sda/device/scsi_disk/*/provisioning_mode   # unmap
   ```
   `udevadm settle` is not optional here. `trigger` queues the uevent and
   returns immediately, so a `cat` on the next line races the rule and reads the
   old value — which looks exactly like the rule failing. `lsblk -D` a moment
   later showing a non-zero `DISC-MAX` is the same evidence arriving after the
   race has resolved. `udevadm test /sys/block/sda/device/scsi_disk/*` explains
   a rule that genuinely does not match.

4. **Make the trim periodic.** The timer ships with `util-linux` and runs
   weekly:
   ```bash
   sudo systemctl enable --now fstrim.timer
   systemctl list-timers fstrim.timer
   ```
   Use the timer, not `discard` in `/etc/fstab`. Continuous discard sends an
   `UNMAP` inline with every delete, through a bridge with a shallow queue, on a
   node where Prometheus deletes constantly. Weekly batch is the default for
   this reason.

5. **Confirm after a reboot**, because the udev rule is the part that silently
   fails:
   ```bash
   sudo reboot
   # then
   cat /sys/block/sda/device/scsi_disk/*/provisioning_mode   # unmap
   lsblk -D                                                  # non-zero
   sudo fstrim -v /                                          # a smaller number
   ```
   The second `fstrim` trimming far less than the first is the expected
   result — the first one reclaimed everything the drive had accumulated since
   it was built.

## Checking it is still working

Nothing scrapes this, and a kernel or firmware update that changes the disk's
enumeration takes it out without a word.

```bash
cat /sys/block/sda/device/scsi_disk/*/provisioning_mode
systemctl status fstrim.service      # last run, and bytes trimmed
journalctl -u fstrim.service | tail
```

An `fstrim.service` that keeps succeeding while reporting `0 B` trimmed is the
same silent failure as never enabling it — it means the udev rule stopped
matching.
