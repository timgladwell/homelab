# Adding a Syslog Source

For an off-cluster device that can send only syslog, such as a printer, a
phone adapter or a network appliance, to send its logs to its site's
collector. A device that speaks OTLP or Loki push uses the collector's HTTPS
ingest points instead (ADR 0003, *Every site publishes these ingest points*).

Each sender is a Component of its own, `base/<sender>-syslog`. It adds its
ports to the site's syslog address (`base/syslog`) and one Alloy file to the
collector, and touches no other sender's files. `base/unifi-syslog` is the
full example, `base/obihai-syslog` the minimal one.

## Pre-checks

1. **The device can send syslog to a remote server.** Find its setting, and
   whether it takes an IP address or a hostname, which port, and UDP, TCP or
   TLS. Plaintext UDP is accepted only from a sender that offers nothing else
   (ADR 0003). A device with no syslog setting can't be added this way.
2. **Which site it's at.** It sends to that site's syslog address
   (`METALLB_SYSLOG_IP`, `10.x.1.81`).
3. **Whether a Component for this kind of device already exists**
   (`ls base/*-syslog`). If one does, the site only needs to list it (step 4).

## Steps

1. **Choose its ports and stream names.** One port per format the device
   sends, and one stream (`job`) per port. Ports in use:

   ```bash
   git grep -n 'port: 15' -- 'base/*-syslog'
   ```

   Take the next free port above them. Name the stream after the device and
   what it sends, in the [naming
   convention](../current-state/naming-convention.md#streams-job), and add it
   to that table.

2. **Create `base/<sender>-syslog`**, copying `base/obihai-syslog`:
   - `service-ports.yaml`: its ports, as a patch on `collector-syslog`;
   - `<sender>-syslog.alloy`: one raw UDP listener per port, labelled with
     `job` and `site = "${SITE_NAME}"`, and `host` from
     `__syslog_connection_ip_address`;
   - `kustomization.yaml`: the patch, and the `configMapGenerator` merge into
     `collector-config`.

   Start raw and unparsed. Don't write parsing for a format nobody has seen.
   Alloy component labels must be unique across every file the collector
   loads, so name them after the sender.

3. **Validate** (the `manifest-validator` subagent). Step 12 checks each
   site's assembled Alloy configuration, so a clash with another receiver
   shows up there.

4. **List the Component in the site's `sites/<site>/monitoring/kustomization.yaml`**,
   under `components:`, after `../../../base/syslog`. Listing it before that
   fails the build, because its patch has no target.

5. **Record the device's setting** in [State That Is Not In
   Git](../current-state/host-state.md), *Off-cluster*: the address, port and
   level it sends to, in the same PR.

6. **Merge, then set the device** to the site's syslog address and the new
   port. At Eastbank, wait for the promotion to `stable` first.

7. **Read its raw lines** and decide what the receiver should do with them:
   - parse the header into `host` and a timestamp if it has one, following
     `base/unifi-syslog`'s lenient pattern (keep every line, mark and count
     the unparsed ones);
   - redact anything secret it writes in plaintext;
   - add a `DROP` for noise (CONTRIBUTING, *Dropped signals*).

   That's a second PR, written against real lines.

## Post-checks

1. **Lines arrive**, with the right `site` and `host`:

   ```logql
   sum by (site, host) (count_over_time({job="<stream>"}[10m]))
   ```

   Nothing at all usually means the device isn't sending, the port isn't on
   the Service, or a typo in the listener's address. Check the rendered
   Service first:

   ```bash
   kubectl -n monitoring get svc collector-syslog -o jsonpath='{.spec.ports[*].port}'
   ```

2. **Nothing else changed:** every other syslog stream at the site still
   arrives at its usual rate.

## Appendix: examples

- **OBi100 at Eastbank** (2026-11-05): the second syslog sender, and the one
  that moved the syslog address out of `base/unifi-syslog` into a Component
  of its own. Port 1518, stream `obihai-syslog`, raw. Its admin guide gives a
  server IP, a port (default 514) and a level (7 is debug). Its line format
  was unknown when the receiver was written.
- **MFC-9130CW printer** (2026-11-05): its Network User's Guide has no syslog
  setting, so it can't be added this way. It answers SNMP.
