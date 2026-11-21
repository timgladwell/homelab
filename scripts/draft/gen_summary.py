#!/usr/bin/env python3
"""Generate the Estate summary prototype dashboard JSON (#614). Throwaway: only the output is ever committed.

  gen_summary.py            dashboard JSON on stdout
  gen_summary.py --exprs    every query, with $site replaced by .+ so it can be run in Explore
"""
import json, sys

DS = {"type": "prometheus", "uid": "${datasource}"}
panels, y, pid = [], 0, 0


def nid():
    global pid
    pid += 1
    return pid


def row(title):
    global y
    panels.append({"type": "row", "id": nid(), "title": title, "collapsed": False,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []})
    y += 1


def target(expr, legend, ref, instant, fmt="time_series"):
    return {"datasource": DS, "expr": expr, "refId": ref, "legendFormat": legend,
            "instant": instant, "range": not instant, "format": fmt}


def place(p, w, h, x):
    p["gridPos"] = {"h": h, "w": w, "x": x, "y": y}
    panels.append(p)


def steps(*pairs):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in pairs]}


RED = steps((None, "green"), (1, "red"))


def by_ref(ref, **props):
    return {"matcher": {"id": "byFrameRefID", "options": ref},
            "properties": [{"id": k, "value": v} for k, v in props.items()]}


def by_name(name, **props):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": k, "value": v} for k, v in props.items()]}


def stat(title, exprs, w, h, x, desc="", unit="none", decimals=0, thresholds=RED, overrides=(), text="value_and_name"):
    """exprs: list of (expr, legend)."""
    place({"type": "stat", "id": nid(), "title": title, "description": desc, "datasource": DS,
           "targets": [target(e, l, chr(65 + i), True) for i, (e, l) in enumerate(exprs)],
           "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                       "colorMode": "background", "graphMode": "none", "textMode": text,
                       "orientation": "horizontal", "justifyMode": "auto"},
           "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "thresholds": thresholds},
                           "overrides": list(overrides)}}, w, h, x)


def ts(title, exprs, w, h, x, desc="", unit="short", right=None, maxv=None, decimals=None, draw="line"):
    """exprs: list of (expr, legend). `right`: refIds drawn as bars on the right axis."""
    overrides = [by_ref(r, **{"custom.axisPlacement": "right", "custom.drawStyle": "bars",
                              "custom.fillOpacity": 60, "unit": "short", "decimals": 0,
                              "color": {"mode": "fixed", "fixedColor": "orange"}}) for r in (right or [])]
    d = {"unit": unit, "min": 0, "custom": {"drawStyle": draw, "fillOpacity": 10, "showPoints": "never"}}
    if maxv is not None:
        d["max"] = maxv
    if decimals is not None:
        d["decimals"] = decimals
    place({"type": "timeseries", "id": nid(), "title": title, "description": desc, "datasource": DS,
           "targets": [target(e, l, chr(65 + i), False) for i, (e, l) in enumerate(exprs)],
           "fieldConfig": {"defaults": d, "overrides": overrides},
           "options": {"legend": {"displayMode": "list", "placement": "bottom"},
                       "tooltip": {"mode": "multi", "sort": "desc"}}}, w, h, x)


def timeline(title, expr, legend, w, h, x, desc=""):
    place({"type": "state-timeline", "id": nid(), "title": title, "description": desc, "datasource": DS,
           "targets": [target(expr, legend, "A", False)],
           "fieldConfig": {"defaults": {"thresholds": RED, "color": {"mode": "thresholds"},
                                        "mappings": [{"type": "value", "options": {"0": {"text": "OK"}, "1": {"text": "Pressure"}}}]},
                           "overrides": []},
           "options": {"showValue": "never", "mergeValues": True, "rowHeight": 0.8,
                       "legend": {"showLegend": False}}}, w, h, x)


def advance(h):
    global y
    y += h


S = 'site=~"$site"'
ZERO = f" or count by (site) (kube_node_info{{{S}}}) * 0"  # 0 per selected site instead of "No data"
C = f'container!="", container!="POD", {S}'
GR = f'gotk_resource_info{{customresource_kind="GitRepository", {S}}}'
KS = f'gotk_resource_info{{customresource_kind="Kustomization", {S}}}'
FAILING = f'gotk_resource_info{{ready="False", reason!="DependencyNotReady", {S}}}'
SHA = '"sha", "$1", "revision", ".*@sha1:(.{7}).*"'

# ── Now ──────────────────────────────────────────────────────────────────────
row("Now")
stat("Alerts firing", [
    (f'count(ALERTS{{alertstate="firing", severity="critical", {S}}}) or vector(0)', "critical"),
    (f'count(ALERTS{{alertstate="firing", severity="warning", {S}}}) or vector(0)', "warning"),
    (f'count(ALERTS{{alertstate="firing", severity="info", {S}}}) or vector(0)', "info"),
], 6, 6, 0, "Firing alerts for the selected sites, by severity. Watchdog and InfoInhibitor (severity none) always fire by "
   "design and are left out. Info never turns red: CPUThrottlingHigh lives there until #618.",
   overrides=[by_ref("B", thresholds=steps((None, "green"), (1, "orange"))),
              by_ref("C", thresholds=steps((None, "green"), (1, "text")))])
ts("Should be zero", [
    ('(sum(increase(alertmanager_notifications_failed_total[5m])) or vector(0)) '
     '+ (sum(increase(prometheus_notifications_errors_total[5m])) or vector(0))', "alert delivery failures (estate)"),
    (f'sum by (site) (round(increase(kube_pod_container_status_restarts_total{{{S}}}[5m]))){ZERO}', "{{site}} restarts"),
    (f'count by (site) (kube_job_failed{{condition="true", {S}}} == 1){ZERO}', "{{site}} failed Jobs"),
    (f'count by (site) (kube_pod_status_phase{{phase=~"Pending|Failed|Unknown", {S}}} == 1){ZERO}', "{{site}} pods not running"),
], 18, 6, 6, "Everything here is 0 almost all the time, so anything else is a spike. Restarts and delivery failures are "
   "counted per 5 minutes. Delivery failures are estate-wide (Alertmanager runs only at Akron, #630) and ignore the site picker.",
   decimals=0)
advance(6)

# ── Deploy ───────────────────────────────────────────────────────────────────
row("Deploy")
# Site status, by precedence: 2 failed > 1 in progress > 0 complete. `or` keeps the left-hand series for a site.
STATUS = (f'((count by (site) ({FAILING}) * 0 + 2)'
          f' or (count by (site) ({KS} unless on (site, revision) {GR}) * 0 + 1)'
          f' or (count by (site) (gotk_resource_info{{ready="False", reason="DependencyNotReady", {S}}}) * 0 + 1)'
          f' or (count by (site) ({GR}) * 0))')
def commits(sel):
    """Short sha and branch from a Flux revision label (`main@sha1:<40 hex>`)."""
    return f'label_replace(label_replace({sel}, {SHA}), "branch", "$1", "revision", "(.*)@sha1:.*")'


# One row per site and commit: every commit a Kustomization has applied, plus the one the GitRepository has fetched.
# The fetched commit gets its row before anything applies it, so a rollout (or an apply failing everywhere) shows from the start.
ROWS = f'(count by (site, branch, sha) ({commits(KS)}) or count by (site, branch, sha) ({commits(GR)}))'
FETCHED = f'label_replace({GR}, {SHA})'
# The fetched commit carries the site's status (transitory: in progress; final: failed); older commits are complete.
ROW_STATUS = (f'((({STATUS}) * on (site) group_right () (({ROWS} and on (site, sha) {FETCHED}) * 0 + 1))'
              f' or (({ROWS} unless on (site, sha) {FETCHED}) * 0))')
# ADR 0002: Akron is the canary, so a commit Akron has neither fetched nor applied means the site is behind it: a mild
# signal. Deliberately not filtered by the site picker.
AKRON = (f'count by (sha) (label_replace(gotk_resource_info{{customresource_kind=~"Kustomization|GitRepository", '
         f'site="akron"}}, {SHA}))')
DEPLOY = (f'max by (site, commit) ('
          f'label_join(label_replace({ROW_STATUS} unless on (sha) {AKRON}, "drift", "drift", "", ""), "commit", ":", "branch", "sha", "drift")'
          f' or label_join({ROW_STATUS} and on (sha) {AKRON}, "commit", ":", "branch", "sha"))')
FAILING_PER_ROW = f'(count by (site) ({FAILING}) or count by (site) ({GR}) * 0) * on (site) group_right () ({DEPLOY} * 0 + 1)'
place({"type": "table", "id": nid(), "title": "Deployment", "datasource": DS,
       "description": "One row per site and applied commit: two rows for a site means it is part-way through a rollout. "
                      "Commit is yellow when Akron, the canary, has neither fetched nor applied it: the site is behind (ADR 0002), a mild signal until promotion. A new commit gets its row as soon as the GitRepository fetches it. "
                      "Status, for the commit the site has fetched: In progress while a Kustomization hasn't applied it or "
                      "waits on a dependency; Failed while any Flux object is Ready=False for another reason. Older commits read Complete. "
                      "To come from Flux events eventually (#615). Not good: Flux objects Ready=False, excluding DependencyNotReady.",
       "targets": [target(DEPLOY, "", "A", True, "table"), target(FAILING_PER_ROW, "", "B", True, "table")],
       "transformations": [
           {"id": "merge", "options": {}},
           {"id": "organize", "options": {
               "excludeByName": {"Time": True},
               "indexByName": {"site": 0, "commit": 1, "Value #A": 2, "Value #B": 3},
               "renameByName": {"site": "Site", "commit": "Commit", "Value #A": "Status", "Value #B": "Not good"}}},
           {"id": "sortBy", "options": {"sort": [{"field": "Site"}]}},
       ],
       "fieldConfig": {"defaults": {"custom": {"align": "left"}, "thresholds": steps((None, "transparent"))},
                       "overrides": [
                           by_name("Commit", **{"custom.cellOptions": {"type": "color-background", "mode": "basic"},
                                                "mappings": [{"type": "regex", "options": {
                                                    "pattern": "^(.*):drift$", "result": {"text": "$1", "color": "yellow"}}}]}),
                           by_name("Status", **{"custom.cellOptions": {"type": "color-background", "mode": "basic"},
                                                "mappings": [{"type": "value", "options": {
                                                    "0": {"text": "Complete", "color": "green"},
                                                    "1": {"text": "In progress", "color": "blue"},
                                                    "2": {"text": "Failed", "color": "red"}}}]}),
                           by_name("Not good", **{"custom.cellOptions": {"type": "color-background", "mode": "basic"},
                                                  "thresholds": RED, "decimals": 0}),
                       ]},
       "options": {"showHeader": True, "cellHeight": "sm"}}, 12, 6, 0)
ts("Kustomization reconcile time", [
    (f'max by (site) (rate(gotk_reconcile_duration_seconds_sum{{kind="Kustomization", {S}}}[5m]) '
     f'/ rate(gotk_reconcile_duration_seconds_count{{kind="Kustomization", {S}}}[5m]))', "{{site}}")
], 12, 6, 12, "Mean reconcile duration. A proxy for deploy time (merge-to-applied has no source yet, #623); "
   "a candidate source for deploy markers and periods (#615).", unit="s")
advance(6)

# ── Budget ───────────────────────────────────────────────────────────────────
row("Budget")
stat("Root disk", [(f'1 - sum by (site) (node_filesystem_avail_bytes{{mountpoint="/", {S}}}) / sum by (site) (node_filesystem_size_bytes{{mountpoint="/", {S}}})', "{{site}}")],
     6, 4, 0, unit="percentunit", thresholds=steps((None, "green"), (0.7, "yellow"), (0.85, "red")))
stat("Containers > 80% memory limit", [
    (f'count by (site) (max by (site, namespace, pod, container) (container_memory_cache{{{C}}} + container_memory_rss{{{C}}}) '
     f'/ max by (site, namespace, pod, container) (kube_pod_container_resource_limits{{resource="memory", {S}}}) > 0.8){ZERO}', "{{site}}")
], 6, 4, 6, "Page cache plus process memory against the limit (memory-sizing runbook). List on Estate overview.",
     thresholds=steps((None, "green"), (1, "yellow")))
timeline("Node pressure conditions",
         f'max by (site, condition) (kube_node_status_condition{{condition=~".*Pressure", status="true", {S}}})',
         "{{site}} {{condition}}", 12, 4, 12, "Red while the kubelet reports MemoryPressure, DiskPressure or PIDPressure: it is about to evict.")
advance(4)
ts("CPU by node", [(f'1 - avg by (site) (rate(node_cpu_seconds_total{{mode="idle", {S}}}[5m]))', "{{site}}")], 8, 7, 0,
   unit="percentunit", maxv=1, decimals=0)
ts("Memory by node", [(f'1 - sum by (site) (node_memory_MemAvailable_bytes{{{S}}}) / sum by (site) (node_memory_MemTotal_bytes{{{S}}})', "{{site}}")],
   8, 7, 8, unit="percentunit", maxv=1, decimals=0)
DISK = f'device!~"loop.*|ram.*", {S}'
ts("Disk IOPS by node", [
    (f'sum by (site) (rate(node_disk_reads_completed_total{{{DISK}}}[5m]) + rate(node_disk_writes_completed_total{{{DISK}}}[5m]))', "{{site}}")
], 8, 7, 16, "Reads plus writes completed per second. Thrash shows as spikes.", unit="iops", decimals=0)
advance(7)

# ── Needing more than allocated ──────────────────────────────────────────────
row("Needing more than allocated")
ts("CPU throttled, by container", [
    (f'sum by (site, namespace, container) (rate(container_cpu_cfs_throttled_periods_total{{{C}}}[5m])) '
     f'/ sum by (site, namespace, container) (rate(container_cpu_cfs_periods_total{{{C}}}[5m])) > 0.05', "{{site}} {{namespace}}/{{container}}")
], 12, 8, 0, "Share of CFS periods in which the container hit its CPU limit. Above 5% shown. CPUThrottlingHigh fires at 25% for 15m.",
   unit="percentunit", maxv=1, decimals=0)
ts("Memory: page-cache refaults, OOM events", [
    (f'sum by (site, namespace, container) (rate(container_memory_workingset_refault_file_total{{{C}}}[5m])) > 1', "{{site}} {{namespace}}/{{container}}"),
    (f'sum by (site, namespace, container) (increase(container_oom_events_total{{{C}}}[5m])) > 0', "OOM {{site}} {{namespace}}/{{container}}"),
], 12, 8, 12, "Pages/s re-read soon after eviction (above 1/s shown): a container near its limit re-reads its own files "
   "(memory-sizing runbook). OOM events as bars, right axis.", decimals=0, right=["B"])
advance(8)
ts("Pressure stall by node", [
    (f'sum by (site) (rate(node_pressure_cpu_waiting_seconds_total{{{S}}}[5m]))', "{{site}} cpu"),
    (f'sum by (site) (rate(node_pressure_memory_waiting_seconds_total{{{S}}}[5m]))', "{{site}} memory"),
    (f'sum by (site) (rate(node_pressure_io_waiting_seconds_total{{{S}}}[5m]))', "{{site}} io"),
], 12, 7, 0, "PSI 'some': the share of wall time at least one task was stalled.", unit="percentunit", decimals=0)
ts("Pressure stall by container (above 1%)", [
    (f'sum by (site, namespace, container) (rate(container_pressure_cpu_waiting_seconds_total{{{C}}}[5m])) > 0.01', "{{site}} cpu {{namespace}}/{{container}}"),
    (f'sum by (site, namespace, container) (rate(container_pressure_memory_waiting_seconds_total{{{C}}}[5m])) > 0.01', "{{site}} memory {{namespace}}/{{container}}"),
    (f'sum by (site, namespace, container) (rate(container_pressure_io_waiting_seconds_total{{{C}}}[5m])) > 0.01', "{{site}} io {{namespace}}/{{container}}"),
], 12, 7, 12, "The same per container: which one is waiting. CPU waiting includes time throttled at its limit.",
   unit="percentunit", decimals=0)
advance(7)

# ── Observability flowing ────────────────────────────────────────────────────
row("Observability flowing")
ts("Samples sent to Akron, per site", [
    (f'sum by (site) (rate(prometheus_remote_storage_samples_total{{{S}}}[5m]))', "{{site}}"),
    (f'sum by (site) (increase(prometheus_remote_storage_samples_failed_total{{{S}}}[5m])) > 0', "{{site}} failed"),
], 8, 7, 0, "Samples/s each site's collector forwards; flat at zero means the site has gone quiet. Failed samples as bars, right axis.",
   decimals=0, right=["B"])
ts("Log bytes sent to Akron, per site", [
    (f'sum by (site) (rate(loki_write_sent_bytes_total{{{S}}}[5m]))', "{{site}}"),
    (f'sum by (site) (increase(loki_write_dropped_bytes_total{{{S}}}[5m])) > 0', "{{site}} dropped"),
], 8, 7, 8, "Dropped log bytes as bars, right axis.", unit="Bps", decimals=0, right=["B"])
ts("Send lag (WAL backlog)", [
    (f'max by (site) (prometheus_remote_storage_highest_timestamp_in_seconds{{{S}}} - ignoring(remote_name, url) group_right '
     f'prometheus_remote_storage_queue_highest_sent_timestamp_seconds{{{S}}})', "{{site}}")
], 8, 7, 16, "How far behind the forwarder is (forwarding-path-recovery runbook). A silent site's lag goes stale, not up.",
   unit="s", decimals=0)
advance(7)

SITE_VAR = {"name": "site", "label": "site", "type": "query", "datasource": DS,
            "definition": "label_values(kube_node_info, site)",
            "query": {"query": "label_values(kube_node_info, site)", "refId": "site"},
            "multi": True, "includeAll": True, "allValue": ".+", "refresh": 1, "sort": 1, "hide": 0,
            "current": {"selected": True, "text": ["All"], "value": ["$__all"]}, "options": [], "regex": "", "skipUrlSync": False}
dash = {
    "uid": "estate-summary-draft", "title": "Estate summary (draft)", "tags": ["estate", "draft"],
    "timezone": "browser", "schemaVersion": 39, "editable": True, "refresh": "1m", "graphTooltip": 1,
    "time": {"from": "now-6h", "to": "now"},
    "templating": {"list": [{"name": "datasource", "label": "datasource", "type": "datasource", "query": "prometheus",
                             "current": {"selected": False, "text": "default", "value": "default"},
                             "hide": 0, "includeAll": False, "multi": False, "options": [], "refresh": 1,
                             "regex": "", "skipUrlSync": False}, SITE_VAR]},
    "annotations": {"list": []}, "links": [], "panels": panels,
}
if sys.argv[1:] == ["--exprs"]:
    for p in panels:
        for t in p.get("targets", []):
            print(f'{p["title"]} [{t["refId"]}]\t{t["expr"].replace("$site", ".+")}')
else:
    json.dump(dash, sys.stdout, indent=2)
    print()
