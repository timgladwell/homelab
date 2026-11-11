# The Log Pipeline

How a log line gets from where it's written to Akron's Loki, and how each
component behaves when something on the way is down, restarting or
reconfigured. Read it to refresh how the pipeline works before changing it or
working an issue in it. The design is
[ADR 0003](../adr/0003-observability-storage-and-stack.md) and
[ADR 0009](../adr/0009-observability-ingest-contract.md). Procedures are in the
runbooks linked below.

Read against Alloy v1.21.3 (chart `~1.15.0`). Recheck the WAL facts when
Alloy's chart moves.

## The path

| Hop | Component | Buffer |
|---|---|---|
| Journal and pod logs → collector | Node log reader (`base/collector/node-log-reader.alloy`), a DaemonSet | None of its own; see below |
| Syslog, OTLP and pushes → collector | Receivers inside the collector | None; a sender loses what it sends while the collector is unreachable |
| Collector → Akron's Loki | `loki.write "default"` in `base/collector/collector.alloy` | The log WAL on the collector's PVC |
| Inside Loki | Loki's ingester | Loki's own WAL, separate from the collector's (#540) |

## The collector's log WAL

- **It is Prometheus's WAL library inside Alloy.** `loki.write` stores
  unsent entries with the same code Prometheus uses for its own write-ahead
  log (`tsdb/wlog`). It is experimental in Alloy, which is why the collector
  runs at `--stability.level=experimental`.
- **It is a directory of numbered segment files**, each up to 128 MB. The size
  is fixed in Alloy's code, and no setting changes it.
- **Segments are append-only and never reused.** A new segment starts when
  the current one is full, and every time the collector process starts.
  Nothing starts one on a schedule.
- **A segment is deleted 24h after its last write** (`max_segment_age`). The
  newest segment is never deleted. So the oldest entry kept can be as old as
  the time it takes to fill a segment, plus 24h:

  | Site | Write rate (rough, one listing on 2026-11-09) | A segment lasts | Oldest entry kept |
  |---|---|---|---|
  | Akron | ~5.4 MB/h | about a day | about 2 days |
  | Eastbank | ~0.9 MB/h | about 6 days | about 7 days, unless the collector restarts first |

  ADR 0003's 24h window is therefore a minimum, never an exact cut-off.
- **The sender retries until the WAL deletes the data.** With
  `max_backoff_retries = 0`, a connection failure, a 5xx or a 429 is retried
  for as long as it takes (#662). Any other 4xx is dropped at once, because
  retrying can't fix it.
- **Memory stays bounded while it waits.** The send queue holds 10 batches
  and then blocks. The WAL keeps growing on disk instead.
- **Progress is a marker that records whole segments**
  (`<wal dir>/remote/segment_marker`). Anything that rebuilds the sender, such
  as a change to the `endpoint` block, replays from the start of the marked
  segment. A config reload that leaves the endpoint unchanged doesn't
  ([Changing a Site's Forwarding Path](../runbooks/forwarding-path-changes.md)).

## The node log reader

- **It has no WAL, because its sources are already on disk.** It reads the
  journal and the pod log files. While the collector is unreachable, its sends
  block and it stops reading, so the files are its buffer.
- **It doesn't record how far it has read.** After its own restart it re-sends
  recent logs, so it doesn't resume exactly where it stopped. Loki rejects the
  re-sent entries that are older than about an hour.

## Drops, and what they mean

- **A replay produces a burst of drops, and if the entries were already
  delivered, that's normal.** Loki rejects replayed entries older than about
  an hour behind each stream with a 400, `entry too far behind`, and the
  sender counts each one as dropped. It happens when a collector's endpoint
  config changes and when a node log reader restarts. The rejection looks the
  same for an entry that never arrived, and that one is loss.
- **The drop counter is the sender's, and its `reason` lumps causes
  together.** `loki_write_dropped_entries_total` is Alloy's metric, not
  Loki's. It has `rate_limited`, `stream_limited`, `queue_is_full` and
  `batch_too_large`. Everything else reads `ingester_error`: a 4xx rejection,
  and retries run out after connection failures or 5xx. The collector's own
  `final error sending batch` log line has the real cause in `status=`
  (`-1` is a connection failure).

## What isn't known

- **No outage has yet tested any of this end to end** (#672).
- **Restarts mid-outage:** the log WAL can lose unsent entries when the
  collector restarts mid-outage (grafana/alloy#7112). Accepted in ADR 0003.
