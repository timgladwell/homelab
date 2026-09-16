# TRIM on a USB-Attached SSD

## Background

Akron boots from an NVMe drive behind a USB3 bridge, and that bridge is the
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

## Why it is worth doing here

Measured on Akron, 2026-09-16, over the preceding 45 days:

| | |
|---|---|
| Root filesystem | `/dev/sda2`, 251.6 GB, 181.0 GB available |
| Sustained writes to `sda` | **17.6 GB/day**, steady between 16 and 21 GB/day across the window |

Two things follow, and the first is the one that decides this.

**The drive rewrites its own capacity every fortnight.** At 17.6 GB/day it takes
about two weeks of ordinary operation to touch all 251 GB of LBA space once.
After that the controller believes every block holds live data — because as far
as it was ever told, every block does — while the filesystem considers 72% of
it free. Garbage collection is then working against nothing but the drive's
factory overprovisioning, and that state is not something Akron is approaching;
at this write rate it arrived months ago.

**Endurance stops being a rounding error.** 17.6 GB/day is 6.4 TB/year of host
writes, so a five-year life is ~32 TB. Multiply by the write amplification of an
untrimmed FTL rather than a trimmed one and the difference is the difference
between comfortably inside a 251 GB-class drive's endurance rating and at or
over it. Nothing here is urgent — but this is no longer a drive where TRIM is
theoretical hygiene.

Worth knowing while reading the above: the workload does **not** obviously
explain the number. Prometheus appends about 0.2 GB/day of raw samples, and even
allowing for WAL and compaction it is a small fraction of 17.6. Where the rest
comes from has not been measured — likely k3s's embedded datastore and
containerd, both of which write continuously — and it is tracked separately. It
does not change anything on this page: the FTL sees the total regardless of
which process produced it.


**Whether this is fixable depends on the bridge**, so the first half of this
page is a diagnosis you cannot skip. Some bridges accept `UNMAP` and discard the
wrong ranges — the reason the kernel is conservative here in the first place —
so "enable it and see" is not a safe move on a drive holding anything.

## What Akron has, diagnosed 2026-09-16

| | |
|---|---|
| Bridge | Realtek RTL9210 M.2 NVMe adapter, USB ID `0bda:9210` |
| Driver | `uas` — not quirked down to `usb-storage` |
| `provisioning_mode` | `full` |
| `lsblk -D` | `DISC-GRAN 0B`, `DISC-MAX 0B` — discard off |
| VPD page B2h | `LBPU=1`, `LBPWS=0`, `LBPWS10=0`, `LBPRZ=0x0`, provisioning type not reported |
| `fstrim.timer` | **enabled, and has been all along** |

**Verdict: supported, and worth enabling.** `LBPU=1` is the bridge explicitly
stating that it implements `UNMAP`, and `uas` is the driver that can issue it.
This is the good case.

**Why the kernel disabled it anyway**, which is the part worth understanding
before overriding it: the kernel does not gate discard on `LBPU`. It gates on
`LBPME` from `READ CAPACITY(16)` — "is this device thinly provisioned" — and
this bridge reports its provisioning type as not known or fully provisioned. So
`sd` concludes there is nothing to unmap and sets `provisioning_mode` to `full`,
never reaching the `LBPU=1` that says otherwise. The override is therefore not
"force a mode the device denied"; it is "act on the capability the device
advertised, which the kernel's conservative default ignored".

`LBPWS=0` and `LBPWS10=0` mean `WRITE SAME` with the unmap bit is not available,
so `unmap` is the only mode that will work here — do not substitute
`writesame_16`.

`LBPRZ=0x0` means discarded blocks are **not** guaranteed to read back as zero.
Harmless for ext4, which never reads a block it has freed, and irrelevant to the
`fstrim` path. It would matter to anything layering dm-crypt or a filesystem
that assumes zeroed reads on this device; nothing here does.

### The silent failure is confirmed, not hypothetical

`fstrim.timer` is enabled on Akron and has been running weekly against a device
with `DISC-MAX 0B`. It has never reclaimed anything.

It has also never complained. Debian's unit runs
`fstrim --listed-in … --verbose --quiet-unsupported`, and `--quiet-unsupported`
is precisely the flag that turns "the discard operation is not supported" into
a silent exit 0. So the timer is green, the service is green, the journal is
unremarkable, and the work is not happening — which is the exact shape
[host-state.md](../host-state.md) exists to inventory.

Nothing would have caught it either. node-exporter's `systemd` collector is
opt-in upstream and nothing here opts in, so no unit state reaches Prometheus
and even a unit that *did* fail every week would be invisible. This is not a
casualty of the cardinality trimming in #287 — that dropped cAdvisor and
kube-apiserver series, and node-exporter is scraped with no drop rules at all.
The collector has simply never been on. That is an alerting gap rather than a
TRIM problem, and belongs with the rest of them in #221.

### What enabling it actually reclaimed

First `fstrim -v /` after setting `provisioning_mode=unmap`, 2026-09-16:

```
/: 178 GiB (191133110272 bytes) trimmed
```

178 GiB against 181 GB the filesystem considered free — so essentially every
free block on the drive was being held as live by the controller, which is the
predicted end state of never having trimmed, confirmed. The canary checksum
matched and `dmesg` showed no ext4 or I/O errors, which is the evidence that
this bridge's `UNMAP` does what it claims.

Expect subsequent weekly runs to report a tiny fraction of this.

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

3. **Persist it with a udev rule**, keyed on the bridge's USB IDs from step 2.
   The setting is per-device and is lost on reboot or replug without this:
   ```bash
   sudo tee /etc/udev/rules.d/10-usb-ssd-trim.rules <<'RULE'
   # Enable TRIM on the USB-attached SSD. The kernel leaves provisioning_mode
   # at "full" for USB bridges, so discard is a no-op without this — see
   # docs/runbooks/usb-trim.md. IDs are the RTL9210 bridge, not the NVMe
   # behind it — replacing the enclosure means updating them.
   ACTION=="add|change", SUBSYSTEM=="scsi_disk", \
     ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="9210", \
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
