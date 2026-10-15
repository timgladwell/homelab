# Taking a Heap Dump of the UniFi Network App

Captures the UniFi Network application's Java heap on a UDR and reads it on a
laptop, to count what the app is holding on to: in particular the gRPC streams
it keeps open to `unifi-core`, which is how a stream leak shows up. The
appendix has a worked example.

Run per console, by hand, over SSH. Nothing here is persistent: the one change
to the box is reverted in the same sitting.

## How the Network app is built, and why that shapes the procedure

- **It is a GraalVM native image, not a JVM.** `/usr/lib/unifi/lib/unifi` is
  Java compiled ahead of time; there is no `libjvm`, no JDK, and no attach
  socket. `jcmd`, `jmap` and `jattach` cannot reach it.
- **It was built with heap-dump support,** so `SIGUSR1` makes it write an
  `.hprof` while it keeps running.
- **The dump goes to the process's working directory,** and no
  `-XX:HeapDumpPath` is set. `unifi.service` sets
  `WorkingDirectory=/usr/lib/unifi/`, which is `755 root:root`, and the app runs
  as `unifi`. Without the temporary permission change below, the dump fails and
  the only trace is a line in `gc.log`:
  `IOException during dumpHeap: Could not create the heap dump file: …`.
- **`gc.log` is the app's stdout** (`StandardOutput=append:…/gc.log` in the
  unit), so anything the dump code prints lands there, between GC lines.
- **Setting `HeapDumpPath` instead is possible but not used.**
  `/etc/default/unifi` is the unit's `EnvironmentFile` and holds
  `UNIFI_NATIVE_OPTS`. It is static (not regenerated at service start), so
  adding `-XX:HeapDumpPath=…` there would survive restarts. It is avoided
  because it is persistent host configuration that a firmware update probably
  overwrites, and because it only takes effect after a Network app restart,
  which empties the heap being investigated.

## Before you start

- **Turn SSH on** in the console's settings. It is normally off; turn it off
  again at the end.
- **Know what the dump contains.** It is everything in the app's memory: admin
  sessions, API tokens, Wi-Fi passphrases, RADIUS secrets. Keep it in one
  directory on the laptop, never in a git checkout, never attached to an issue,
  a forum post or a support ticket. Post counts, not files.
- **The app pauses while it writes,** a few seconds for ~370 MB. Routing,
  Wi-Fi and Protect are unaffected; the Network UI and API stall briefly, and
  Unpoller may log one failed poll.
- **Laptop:** Python 3 for the stream count. Only the fallback, reading it by
  hand, needs Eclipse Memory Analyzer (MAT) and a Java runtime.
  - **MAT:** download it from <https://eclipse.dev/mat/download/>. That is how
    it has been installed here; the `memoryanalyzer` Homebrew cask exists but
    is untested.
  - **Java:** MAT needs a Java runtime, which macOS does not ship. Without one,
    MAT fails to open the dump with no error message at all. Temurin is the one
    used here; other Java installs may work but are untested.

    ```sh
    brew install --cask temurin
    ```

### Pre-checks (on the UDR)

```sh
P=$(systemctl show unifi -p MainPID --value); echo "MainPID=$P"
ls -l /proc/$P/exe                  # expect /usr/lib/unifi/lib/unifi
ps -o etimes= -p $P                 # Network app uptime, seconds
ps -o etimes= -p $(pidof unifi-core)  # unifi-core uptime, seconds
```

Confirm the app still handles `SIGUSR1`. **If this prints `0`, stop:** the
signal's default action would kill the Network app, so a later build has
dropped the heap-dump support and this runbook no longer applies.

```sh
echo $(( 0x$(awk '/SigCgt/{print $2}' /proc/$P/status) >> 9 & 1 ))   # must print 1
```

Check space for the dump (at most about the heap cap, `-Xmx384M`) and record
the current state of the directory and of the log:

```sh
df -h /usr/lib/unifi                     # root overlay; needs ~400 MB free
stat -c '%a %U:%G' /usr/lib/unifi        # expect 755 root:root; restore to whatever this says
grep -n dumpHeap /data/unifi/logs/gc.log # note any existing lines, so a new one stands out
```

## Take the dump

Run these as **three separate pastes**. Pasting them together reverts the
permissions before the app gets to create the file, a second or two after the
signal (it runs a full GC first).

**1. Open the directory to the app and send the signal:**

```sh
P=$(systemctl show unifi -p MainPID --value)
chgrp unifi /usr/lib/unifi && chmod 775 /usr/lib/unifi
kill -USR1 $P
```

**2. Watch it finish.** Repeat every ~10 s until the size stops changing:

```sh
ls -la /usr/lib/unifi/*.hprof; grep -n dumpHeap /data/unifi/logs/gc.log | tail -2
```

The file is named `svm-heapdump-<pid>-<epoch-ms>.hprof`, mode `600`, owned by
`unifi`. A new `dumpHeap` line in `gc.log` means it failed; read it before
retrying.

**3. Restore the directory** — only once the size is stable:

```sh
chgrp root /usr/lib/unifi && chmod 755 /usr/lib/unifi && stat -c '%a %U:%G' /usr/lib/unifi
```

**4. Copy it off and delete it from the UDR** (from the laptop):

```sh
mkdir -p ~/heapdumps
scp 'root@<udr-ip>:/usr/lib/unifi/svm-heapdump-*.hprof' ~/heapdumps/
ssh root@<udr-ip> 'rm /usr/lib/unifi/svm-heapdump-*.hprof; ls /usr/lib/unifi/*.hprof'
```

The final `ls` must report *No such file or directory*.

## Count the streams

From the repo checkout on the laptop:

```sh
python3 -I scripts/hprof-grpc-streams.py ~/heapdumps/svm-heapdump-*.hprof
```

It takes ~20 s for a ~350 MB dump and prints only counts and gRPC method names,
so the output can be posted. It prints two blocks:

- **Instance counts of the `io/grpc/okhttp/OkHttpClient*` classes.**
  `OkHttpClientTransport` is one per gRPC connection; one is normal, the
  connection to `unifi-core` on `127.0.0.1:11051`.
- **Open `OkHttpClientStream`s by gRPC method.** A method with one row per
  call it has ever made is leaking; see *Reading the numbers*.

If it exits with `unknown heap sub-record`, a newer build writes a record the
script doesn't parse. Read the dump in MAT instead.

## Read it in MAT (fallback, and for sizes)

The script counts streams but doesn't size them. MAT gives the retained size,
and is the fallback when the script can't parse a dump.

File → Open Heap Dump → *Leak Suspects Report* can be skipped. The first parse
takes a few minutes and writes index files next to the `.hprof`.

1. **Count the streams.** Histogram → type `OkHttpClientStream` in the first
   row. The `Objects` column of `io.grpc.okhttp.OkHttpClientStream` is the
   number of open gRPC streams the app holds.
2. **Size them.** Dominator Tree → filter `OkHttpClientTransport`. The
   `io.grpc.okhttp.OkHttpClientTransport` instance's *Retained Heap* is what the
   connection to `unifi-core` holds. Expanding a stream shows
   `authority 127.0.0.1:11051`, `unifi-core`'s gRPC port.
3. **Split them by method.** Right-click `OkHttpClientStream` → List objects →
   with outgoing references, expand one, and follow `method` → `fullMethodName`
   to the method's name string. Right-click that string → List objects → with
   incoming references, and expand its `MethodDescriptor`. The total under it
   counts every reference to the method: one from the class's static field, one
   from an array, and **three per open stream**. So `(total − 2) / 3` is that
   method's stream count.

## Reading the numbers

Most gRPC calls close their stream when they finish, so a healthy app holds a
handful: long-lived subscriptions such as `ConsoleAPI/ConsoleStatus`, plus any
calls in flight. A method whose count keeps up with **the number of calls made
to it** is leaking one stream per call.

Count calls from whichever restarted most recently, `unifi-core` or the Network
app: either restart drops the connection and frees every stream. The pre-check
uptimes give that starting point. To get a number big enough to read without
waiting days, call the API that triggers the method at a known rate before
taking the dump (an example is in the appendix).

- **Count ≈ calls since the later restart:** leaking.
- **A handful, after many calls:** not leaking, or nothing called the method.
  Check which before drawing a conclusion.

## A cheaper check without a dump

The Network app's GC log shows the growth of anything it retains, with no
permission change and no dump file. After each full GC the heap is down to
what is actually live, so the lowest post-GC size in each window is a floor
that rises with a leak and drops when whatever holds the leaked objects is
restarted. Lines are timestamped in seconds since the app started:

```sh
for f in /data/unifi/logs/gc.log.1 /data/unifi/logs/gc.log; do echo "== $f"; awk '{t=substr($1,2)+0} t<last{if(cb!="")printf "%5.0fh %7.2f\n",cb*6,mn; print "-- restart: " last "s -> " t "s"; cb=""} {last=t} /Full GC/{split($(NF-1),a,"->"); v=a[2]+0; b=int(t/21600); if(cb==""||b!=cb){if(cb!="")printf "%5.0fh %7.2f\n",cb*6,mn; cb=b; mn=v} else if(v<mn)mn=v} END{if(cb!="")printf "%5.0fh %7.2f\n",cb*6,mn}' "$f"; done
```

Each row is a 6-hour window and its lowest post-full-GC heap in MB. A
`-- restart` line is the app starting again (several in a row are its
start-up sequence); a line that is not a GC record also resets the timestamp, so
check one that appears mid-run before reading it as a restart. A steady rise
across windows, with a step down at each restart, is a leak; its slope is the
leak rate.

## Post-checks

- On the UDR: `stat -c '%a %U:%G' /usr/lib/unifi` prints `755 root:root`, and
  `ls /usr/lib/unifi/*.hprof` finds nothing.
- `systemctl is-active unifi` is `active`, with the same `MainPID` as before.
- SSH is turned off again for the console.
- On the laptop: `rm -r ~/heapdumps` once the numbers are recorded; the MAT
  index files are inside it.

## Appendix: example — the `GetDeviceTags` stream leak

This runbook was first used to measure a leak in which every
`unifi.core.console.v1.DeviceTagsAPI/GetDeviceTags` call left a stream open
(#388). The REST call that triggers it is
`GET /proxy/network/v2/api/site/default/device-tags`. Unpoller makes it twice
per poll cycle.

**Driving it at a known rate:** one call every 2 s for an hour, from a
read-only account, before the dump.

```sh
jar=$(mktemp); read -r U; read -rs P
curl -sk -c $jar -H 'Content-Type: application/json' \
  -d "{\"username\":\"$U\",\"password\":\"$P\"}" https://<udr-ip>/api/auth/login -o /dev/null -w '%{http_code}\n'
end=$((SECONDS+3600)); n=0
while [ $SECONDS -lt $end ]; do
  curl -sk -b $jar https://<udr-ip>/proxy/network/v2/api/site/default/device-tags -o /dev/null -w '%{http_code} '
  n=$((n+1)); sleep 2; done; echo; echo calls=$n; rm $jar
```

**The count:** UniFi OS 6.0.12, Network 10.6.106, after 1,715 calls plus 3.3 h
of polling at 5 min (~80 more):

```
   1798  io/grpc/okhttp/OkHttpClientStream
   1798  io/grpc/okhttp/OkHttpClientStream$TransportState
   1798  io/grpc/okhttp/OkHttpClientStream$Sink
      1  io/grpc/okhttp/OkHttpClientTransport
      …
-- OkHttpClientStream by method
   1797  unifi.core.console.v1.DeviceTagsAPI/GetDeviceTags
      1  unifi.core.console.v1.ConsoleAPI/ConsoleStatus
```

1,797 against ~1,795 calls: one leaked stream per call. Each held about
3.6 KB on the Network app's side. In the GC log, the same leak was a floor
rising ~10 MB a day at a 60 s poll interval and ~2 MB a day at 5 min, dropping
by the accumulated amount at each `unifi-core` restart.
