#!/usr/bin/env bash
# Dump NetworkOptimizer's configuration (its settings live in SQLite, not in
# git) as JSON Lines: one {"database"} line per database, then one line per
# table. Run where kubectl reaches the site:
#
#   ./scripts/network-optimizer-config-dump.sh > network-optimizer-config.jsonl
#   jq -c 'select(.table == "SystemSettings") | .rows[]' network-optimizer-config.jsonl
#
# Read-only: sqlite3 -readonly inside the running pod, as the app user, so no
# root-owned -wal/-shm file appears (#520). Covers the main database and every
# sites/<slug>/ database.
#
# Observation, history and cache tables (threat events, roam records, signal
# logs...) and the ASP.NET Identity tables are not dumped, only counted. The
# split follows upstream's DbContext at v2.9.3; a table it doesn't name is
# dumped, so a new upstream table shows up rather than vanishing.
#
# Redacted: any value starting ENC: (upstream's encrypted credentials), any
# column whose name looks secret, and SystemSettings values whose key does.
# Free text can still carry a secret (a webhook URL with a token in it), so read
# the output before it goes anywhere.
set -euo pipefail

NAMESPACE="${NAMESPACE:-apps}"
TARGET="${TARGET:-deploy/network-optimizer}"

kubectl -n "$NAMESPACE" exec -i "$TARGET" -c network-optimizer -- gosu app sh -s <<'POD'
set -eu
cd /app/data

# Builds one SELECT per table from the schema, then runs them. json_object takes
# at most 127 arguments, so a table over 63 columns is reported, not dumped.
generate() {
  sqlite3 -readonly "$1" <<'SQL'
WITH cols AS (
  SELECT m.name AS t, p.cid, p.name AS c,
         '"' || replace(p.name, '"', '""') || '"' AS qc
  FROM sqlite_master m, pragma_table_info(m.name) p
  WHERE m.type = 'table'
  ORDER BY m.name, p.cid
), exprs AS (
  SELECT t, quote(c) || ', ' || CASE
    WHEN lower(c) GLOB '*password*' OR lower(c) GLOB '*secret*'
      OR lower(c) GLOB '*token*' OR lower(c) GLOB '*apikey*'
      OR lower(c) GLOB '*privatekey*' OR lower(c) GLOB '*licensekey*'
      OR lower(c) GLOB '*passphrase*' OR lower(c) GLOB '*hash*'
      THEN 'CASE WHEN ' || qc || ' IS NULL THEN NULL ELSE ''<redacted>'' END'
    WHEN t = 'SystemSettings' AND c = 'Value'
      THEN 'CASE WHEN lower("Key") GLOB ''*token*'' OR lower("Key") GLOB ''*password*''
              OR lower("Key") GLOB ''*secret*'' OR lower("Key") GLOB ''*api_key*''
              OR lower("Key") GLOB ''*apikey*'' OR lower("Key") GLOB ''*key''
              THEN ''<redacted>'' WHEN "Value" LIKE ''ENC:%'' THEN ''<encrypted>''
              ELSE "Value" END'
    ELSE 'CASE WHEN typeof(' || qc || ') = ''blob'' THEN ''<blob '' || length(' || qc || ') || '' bytes>''
              WHEN ' || qc || ' LIKE ''ENC:%'' THEN ''<encrypted>'' ELSE ' || qc || ' END'
  END AS e
  FROM cols
)
SELECT CASE
  WHEN t GLOB 'sqlite_*' THEN ''
  WHEN t GLOB 'AspNet*' OR t IN (
    'AuditEvents', 'AuditResults', 'AlertHistory', 'AlertIncidents',
    'ApAgentEventCursors', 'ApChannelChanges', 'ApChannelOutcomes',
    'ApNeighborSightings', 'ApRadioHealthSamples', 'ApRoamRecords',
    'ClientSignalLogs', 'CrowdSecReputations', 'FirmwareModelTimings',
    'FloorPlanImages', 'HogRowBaselines', 'OuiVendors', 'SharedFirmwareBuilds',
    'SharedNetworkAppBuilds', 'SharedNetworkAppPackages', 'SharedUniFiOsBuilds',
    'SqmBaselines', 'SqmLearningSamples', 'ThreatEvents', 'ThreatPatterns',
    'UpstreamDiscoveries', 'WanDataUsageHistory', 'WanDataUsageSnapshots')
    THEN 'SELECT json_object(''table'', ' || quote(t) || ', ''skipped'', ''data'', ''count'', count(*)) FROM "' || replace(t, '"', '""') || '";'
  WHEN count(*) > 63
    THEN 'SELECT json_object(''table'', ' || quote(t) || ', ''skipped'', ''over 63 columns'');'
  ELSE 'SELECT json_object(''table'', ' || quote(t) || ', ''rows'', json_group_array(json_object(' || group_concat(e, ', ') || '))) FROM "' || replace(t, '"', '""') || '";'
END
FROM exprs GROUP BY t ORDER BY t;
SQL
}

for db in network_optimizer.db sites/*/network_optimizer.db; do
  [ -f "$db" ] || continue
  printf '{"database":"%s"}\n' "$db"
  generate "$db" | sqlite3 -readonly "$db"
done
POD
